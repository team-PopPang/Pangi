# Pangi Admin UI source

이 디렉터리는 Admin UI의 소스 전용 경계다. 빌드 결과만 `src/pangi/web/static`으로 복사해 wheel에 포함한다. WBS 04에서 프레임워크와 빌드 도구를 확정하기 전까지 의존성이나 생성 산출물은 추가하지 않는다.

예정한 소스 경계는 다음과 같다.

- `src/app`: 애플리케이션 진입점과 라우팅
- `src/components`: 공용 표현 컴포넌트
- `src/features`: 기능 단위 UI
- `src/api`: Admin API 클라이언트
- `src/styles`: 토큰과 전역 스타일

