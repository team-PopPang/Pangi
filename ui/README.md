# Pangi Admin UI source

이 디렉터리는 React/Vite 기반 Admin UI의 소스 전용 경계다. 빌드 결과만 `src/pangi/web/static`으로 복사해 wheel에 포함하며 운영 환경에는 Node.js가 필요하지 않다.

예정한 소스 경계는 다음과 같다.

- `src/app`: 애플리케이션 Shell과 라우팅
- `src/components`: 공용 표현 컴포넌트
- `src/features`: 기능 단위 UI
- `src/api`: Admin API 클라이언트
- `src/styles`: 토큰과 전역 스타일

```bash
npm ci
npm run check
npm run build
```
