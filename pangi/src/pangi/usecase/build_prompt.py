from __future__ import annotations

from pangi.domain.models import AgentJob
from pangi.prompts.loader import load_prompt


def build_read_only_analysis_prompt(job: AgentJob, *, repo_path: str) -> str:
    agent_prompt = load_prompt("pangi_agent.md")
    analysis_prompt = load_prompt("read_only_analysis.md")
    return f"""\
{agent_prompt}

{analysis_prompt}
요청 정보:
- job_id: {job.id}
- repo_key: {job.repo_key}
- repo_path: {repo_path}
- requester_user_id: {job.requester_user_id}

사용자 요청:
{job.prompt}
"""
