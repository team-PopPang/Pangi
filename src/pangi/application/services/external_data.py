"""Normalize, redact, envelope, and render explicitly untrusted external text."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from html import escape
from html.parser import HTMLParser

from pangi.application.contracts.external_data import (
    ExternalDataEnvelope,
    ExternalDataError,
    ExternalDataErrorCode,
    ExternalDataMediaType,
    ExternalDataPolicy,
    RenderedExternalData,
)
from pangi.application.services.redaction import RedactionService

_SOURCE_KIND = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")
_BLOCKED_HTML_TAGS = frozenset(
    {"script", "style", "form", "template", "noscript", "iframe", "object", "embed"}
)
_BLOCK_HTML_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
_VOID_HTML_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}
)


class _VisibleHTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.removed_element_count = 0
        self._suppressed_tag: str | None = None
        self._suppressed_tag_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.casefold()
        if self._suppressed_tag is not None:
            if normalized_tag == self._suppressed_tag:
                self._suppressed_tag_depth += 1
            return
        if normalized_tag in _BLOCKED_HTML_TAGS or self._is_hidden(attrs):
            self.removed_element_count += 1
            if normalized_tag not in _VOID_HTML_TAGS:
                self._suppressed_tag = normalized_tag
                self._suppressed_tag_depth = 1
            return
        if normalized_tag in _BLOCK_HTML_TAGS:
            self.parts.append("\n")

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.casefold()
        if self._suppressed_tag is not None:
            return
        if normalized_tag in _BLOCKED_HTML_TAGS or self._is_hidden(attrs):
            self.removed_element_count += 1
            return
        if normalized_tag in _BLOCK_HTML_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if self._suppressed_tag is not None:
            if normalized_tag == self._suppressed_tag:
                self._suppressed_tag_depth -= 1
                if self._suppressed_tag_depth == 0:
                    self._suppressed_tag = None
            return
        if normalized_tag in _BLOCK_HTML_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._suppressed_tag is None:
            self.parts.append(data)

    @staticmethod
    def _is_hidden(attrs: list[tuple[str, str | None]]) -> bool:
        normalized = {name.casefold(): value or "" for name, value in attrs}
        if "hidden" in normalized:
            return True
        if normalized.get("aria-hidden", "").casefold() == "true":
            return True
        style = re.sub(r"\s+", "", normalized.get("style", "").casefold())
        return "display:none" in style or "visibility:hidden" in style


class ExternalDataService:
    """Create an untrusted envelope without retaining the source content."""

    def __init__(
        self,
        policy: ExternalDataPolicy,
        *,
        redactor: RedactionService,
    ) -> None:
        self._policy = policy
        self._redactor = redactor

    def envelope(
        self,
        *,
        source_kind: str,
        media_type: ExternalDataMediaType | str,
        content: str,
    ) -> ExternalDataEnvelope:
        if _SOURCE_KIND.fullmatch(source_kind) is None:
            raise ValueError("source_kind must be a stable lowercase identifier")
        try:
            normalized_media_type = ExternalDataMediaType(media_type)
        except ValueError:
            raise ExternalDataError(ExternalDataErrorCode.INVALID_MEDIA_TYPE) from None
        if len(content.encode()) > self._policy.max_input_bytes:
            raise ExternalDataError(ExternalDataErrorCode.INPUT_BYTES_EXCEEDED)

        removed_html_elements = 0
        normalized = content
        if normalized_media_type is ExternalDataMediaType.HTML:
            parser = _VisibleHTMLTextExtractor()
            parser.feed(normalized)
            parser.close()
            normalized = "".join(parser.parts)
            removed_html_elements = parser.removed_element_count
        normalized = unicodedata.normalize(
            "NFC",
            normalized.replace("\r\n", "\n").replace("\r", "\n"),
        )
        normalized, removed_characters = self._remove_prohibited_characters(normalized)
        if normalized_media_type is ExternalDataMediaType.HTML:
            normalized = self._normalize_html_whitespace(normalized)
        if not normalized.strip():
            raise ExternalDataError(ExternalDataErrorCode.EMPTY_CONTENT)

        redacted = self._redactor.redact_text(normalized)
        if not isinstance(redacted.value, str):
            raise RuntimeError("Redaction service returned an invalid text result")
        safe_content = redacted.value
        content_bytes = len(safe_content.encode())
        if content_bytes > self._policy.max_output_bytes:
            raise ExternalDataError(ExternalDataErrorCode.OUTPUT_BYTES_EXCEEDED)
        return ExternalDataEnvelope(
            source_kind=source_kind,
            original_media_type=normalized_media_type,
            content=safe_content,
            content_fingerprint=hashlib.sha256(safe_content.encode()).hexdigest(),
            content_bytes=content_bytes,
            normalization_policy_version=self._policy.policy_version,
            normalization_policy_fingerprint=self._policy.fingerprint,
            unicode_policy_version=self._policy.unicode_policy_version,
            removed_character_count=removed_characters,
            removed_html_element_count=removed_html_elements,
            redaction=redacted.summary,
        )

    @staticmethod
    def render(envelope: ExternalDataEnvelope) -> RenderedExternalData:
        source = escape(envelope.source_kind, quote=True)
        content = escape(envelope.content, quote=False)
        markup = (
            f'<external_data source="{source}" trust="untrusted" '
            f'content_fingerprint="{envelope.content_fingerprint}">\n'
            f"{content}\n"
            "</external_data>"
        )
        return RenderedExternalData(markup, envelope.content_fingerprint)

    def _remove_prohibited_characters(self, value: str) -> tuple[str, int]:
        kept: list[str] = []
        removed = 0
        for character in value:
            prohibited = ord(character) in self._policy.prohibited_codepoints
            control = unicodedata.category(character) == "Cc" and character not in {"\t", "\n"}
            if prohibited or control:
                removed += 1
                continue
            kept.append(character)
        return "".join(kept), removed

    @staticmethod
    def _normalize_html_whitespace(value: str) -> str:
        lines: list[str] = []
        for raw_line in value.splitlines():
            line = re.sub(r"[\t\f\v ]+", " ", raw_line).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)
