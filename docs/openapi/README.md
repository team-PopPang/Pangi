# Admin API 계약 생성

`pangi-admin-api.json`은 실행 중인 서버에서 내려받는 문서가 아니라 FastAPI Route와 Pydantic Model에서 빌드 시 생성하는 계약이다. 운영 App의 `/openapi.json`, `/docs`, `/redoc`은 계속 비활성화한다.

## 생성 순서

저장소 루트에서 OpenAPI JSON을 먼저 만들고, 그 결과로 Frontend Type을 만든다.

```bash
.venv/bin/python scripts/export_openapi.py
cd ui
npm run api:generate
```

생성된 `pangi-admin-api.json`과 `ui/src/api/generated.ts`는 모두 Commit한다. 두 파일을 직접 수정하지 않는다.

## Drift 확인

```bash
.venv/bin/python scripts/export_openapi.py --check
cd ui
npm run api:check
```

OpenAPI 생성은 Schema 전용 Dependency만 조립한다. Runtime 설정이나 Secret을 읽지 않고, SQLite를 시작하거나 Runtime Data Directory를 만들지 않는다.
