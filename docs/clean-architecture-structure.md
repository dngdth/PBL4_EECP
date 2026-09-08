# Cấu trúc Clean Architecture của EECP

Tài liệu này là quy ước tổ chức code cho Exam Environment Control Platform. Cấu trúc được rút gọn theo đúng nhu cầu hiện tại; không tạo layer hoặc service trống chỉ để giống sơ đồ.

## Cây thư mục

```text
apps/
├── api/
│   ├── app/
│   │   ├── domain/
│   │   │   ├── entities/
│   │   │   │   ├── exam_session.py
│   │   │   │   └── operations.py
│   │   │   ├── value_objects/enums.py
│   │   │   ├── services/policies.py
│   │   │   ├── interfaces/unit_of_work.py
│   │   │   └── exceptions/errors.py
│   │   ├── application/
│   │   │   ├── dtos/exam_pipeline.py
│   │   │   └── use_cases/exam_sessions/pipeline.py
│   │   ├── infrastructure/
│   │   │   ├── persistence/database.py
│   │   │   ├── repositories/sqlite.py
│   │   │   └── di/container.py
│   │   ├── presentation/
│   │   │   ├── api/
│   │   │   │   ├── routers/exam_sessions.py
│   │   │   │   ├── deps.py
│   │   │   │   └── exceptions.py
│   │   │   └── schemas/exam_pipeline.py
│   │   ├── config.py
│   │   └── main.py
│   ├── scripts/demo_pipeline.py
│   └── test/
│       ├── unit/
│       └── integration/
└── web/
    ├── app/                         # Next.js routes/layout
    ├── features/exam-sessions/      # UI, query và type theo nghiệp vụ
    ├── components/ui/               # UI dùng chung
    └── lib/                         # API client và config kỹ thuật
```

## Dependency rule backend

```text
presentation ──┐
               ├──> application ──> domain
infrastructure ┘
```

- `domain` không import FastAPI, Pydantic, SQLite hoặc SDK bên ngoài.
- `application` chỉ điều phối domain và port trong `domain/interfaces`.
- `infrastructure` triển khai port; entity không biết cách dữ liệu được lưu.
- `presentation` chuyển HTTP request/response, không chứa state transition.
- `config.py`, `infrastructure/di` và `main.py` là composition root.

Luồng gọi:

```text
HTTP request
  → presentation/api/router
  → application/use_case
  → domain entity/service/interface
  → infrastructure repository/unit-of-work
  → SQLite (sau này có thể thay PostgreSQL)
```

## Quy tắc frontend

```text
app → features → components/lib
```

- `app` chỉ khai báo route, layout và lắp ghép màn hình.
- Code riêng của quản lý ca thi nằm trong `features/exam-sessions`.
- Component thật sự dùng chung nằm trong `components`.
- API client, URL và tiện ích kỹ thuật nằm trong `lib`.
- Feature không import code nội bộ của feature khác.

## Vị trí đặt code mới

| Loại code | Vị trí |
|---|---|
| State transition, invariant, entity | `apps/api/app/domain/entities` |
| Enum/value object | `apps/api/app/domain/value_objects` |
| Rule thuần phối hợp nhiều entity | `apps/api/app/domain/services` |
| Repository/service contract | `apps/api/app/domain/interfaces` |
| Input/output độc lập HTTP | `apps/api/app/application/dtos` |
| Một luồng hành động | `apps/api/app/application/use_cases/<feature>` |
| Database engine/schema | `apps/api/app/infrastructure/persistence` |
| Repository/Unit of Work adapter | `apps/api/app/infrastructure/repositories` |
| FastAPI endpoint/dependency/error mapping | `apps/api/app/presentation/api` |
| Pydantic HTTP contract | `apps/api/app/presentation/schemas` |
| Next.js route | `apps/web/app` |
| UI/query/type theo nghiệp vụ | `apps/web/features/<feature>` |
| UI/API client dùng chung | `apps/web/components` hoặc `apps/web/lib` |

## Worker

Chưa tạo `apps/worker` vì hiện không có job nền độc lập. Khi có nhu cầu như xử lý AI/RAG, tổng hợp báo cáo lớn hoặc retry command dài hạn, worker mới nên được tách và cũng tuân theo `domain/application/infrastructure/presentation` nếu nghiệp vụ đủ phức tạp.

## Ranh giới đã xác minh sau Phase 3

Policy Management được chia theo trách nhiệm như sau:

```text
apps/api/app/
├── domain/
│   └── services/
│       ├── policies.py             # incident/audit business rules
│       └── policy_profiles.py      # profile model, validation, built-in seed
├── application/
│   ├── dtos/policies.py
│   └── use_cases/policies/
│       └── management.py           # profile CRUD, pending command, ACK
├── infrastructure/
│   ├── repositories/sqlite.py      # profile/command persistence adapters
│   └── di/container.py             # dependency composition
└── presentation/
    ├── api/routers/policies.py     # HTTP policy/command endpoints
    ├── schemas/policies.py         # Pydantic contracts
    └── serializers/policy_yaml.py  # YAML preview formatting
```

Các quyết định quan trọng:

- Domain giữ profile và rule vì đây là chính sách nghiệp vụ, nhưng không biết YAML/HTTP.
- YAML chỉ là representation phục vụ người dùng nên nằm ở `presentation`.
- `ExamPipelineService` chỉ điều phối gateway pipeline; ACK dùng chung nằm trong policy use case và dispatch theo loại session.
- SQLite profile và command repositories vẫn là output adapters của ports trong domain.

Frontend tuân theo `app -> features -> components/lib`:

- Server Actions của exam session nằm trong `features/exam-sessions/actions.ts`.
- Server Actions quản lý profile nằm trong `features/policy-profiles/actions.ts`.
- Feature không import `app` và không import nội bộ của feature khác.
- Auto refresh dùng chung nằm trong `components/ui/auto-refresh.tsx`.

Workstation Agent cũng dùng ports-and-adapters thu gọn:

```text
agent/
├── domain/          # identity và policy contract thuần
├── application/     # heartbeat loop và command orchestration qua Protocol
├── infrastructure/  # HTTP, system identity, Windows/audit enforcement
├── config.py
└── main.py          # composition root
```

Các ranh giới này được khóa bằng
`apps/api/test/unit/test_architecture_boundaries.py`. Test sẽ fail nếu domain
phụ thuộc outer layer, application phụ thuộc infrastructure/presentation, hoặc
một frontend feature import `app`/feature khác.
