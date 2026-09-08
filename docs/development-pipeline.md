# Pipeline phát triển dự án EECP

> Tài liệu làm việc cho nhóm 3 người, áp dụng từ lúc khởi tạo repository đến khi demo được sản phẩm **Exam Environment Control Platform (EECP)**.

## 1. Mục tiêu bản demo

Bản demo phải thể hiện được một ca thi hoàn chỉnh:

```text
Tạo ca thi
  → triển khai policy
  → Gateway/Agent nhận lệnh và phản hồi (ACK)
  → kiểm tra máy trước giờ thi (preflight)
  → bắt đầu ca thi
  → nhận telemetry và phát hiện sự cố
  → kết thúc ca thi
  → khôi phục cấu hình ban đầu
  → xem báo cáo tổng kết và audit log
```

Phạm vi MVP:

- Backend FastAPI cung cấp API cho toàn bộ vòng đời ca thi.
- Dashboard Next.js cho phép giám thị thao tác và theo dõi trạng thái.
- Agent/Gateway tối thiểu có simulator để chứng minh luồng nhận lệnh, ACK, preflight và telemetry.
- Dữ liệu được lưu bền vững; môi trường demo có thể khởi chạy bằng Docker Compose.
- Các luật nghiệp vụ quan trọng có unit test; luồng demo có integration/end-to-end test.

Ngoài phạm vi MVP: triển khai thật trên nhiều phòng máy, AI/RAG, phân tích nâng cao, mobile app và tối ưu cho tải lớn. Chỉ đưa vào khi toàn bộ tiêu chí demo đã đạt.

## 2. Nguyên tắc Clean Architecture

### Backend

```text
presentation ──┐
               ├──> application ──> domain
infrastructure ┘
```

| Layer | Trách nhiệm | Không được chứa |
|---|---|---|
| `domain` | Entity, value object, invariant, state transition, domain service, port/interface | FastAPI, Pydantic, SQLite hoặc SDK ngoài |
| `application` | Use case, DTO độc lập HTTP, điều phối transaction và port | Chi tiết database, HTTP hoặc UI |
| `infrastructure` | Repository, Unit of Work, database, adapter và DI | Luật nghiệp vụ cốt lõi |
| `presentation` | Router, HTTP schema, dependency và ánh xạ lỗi | State transition hoặc truy vấn SQL trực tiếp |

Luồng phụ thuộc bắt buộc:

```text
HTTP request
  → presentation/router
  → application/use_case
  → domain entity/service/interface
  → infrastructure adapter
  → database hoặc hệ thống ngoài
```

### Frontend

```text
app → features → components/lib
```

- `app/` chỉ khai báo route, layout và lắp ghép màn hình.
- Mỗi nghiệp vụ nằm trong `features/<feature>` gồm UI, type và logic gọi API của feature đó.
- UI thật sự dùng chung đặt trong `components/`; API client và tiện ích kỹ thuật đặt trong `lib/`.
- Không import code nội bộ trực tiếp giữa hai feature.

Chi tiết vị trí đặt code xem tại [clean-architecture-structure.md](clean-architecture-structure.md).

## 3. Phân công nhóm 3 người

Thay `TV1`, `TV2`, `TV3` bằng tên thật của thành viên khi chốt nhóm. Mỗi người có vùng phụ trách chính nhưng đều phải viết code, tạo branch, push code và có PR được merge vào `main` trong mỗi mốc.

| Thành viên | Vai trò chính | Phạm vi code chính | Trách nhiệm bổ sung |
|---|---|---|---|
| **TV1 – Backend/Domain** | Xây dựng nghiệp vụ cốt lõi | `domain`, `application`, API contract và unit test | Giữ đúng state machine, review PR liên quan nghiệp vụ |
| **TV2 – Frontend/UX** | Xây dựng dashboard giám thị | `apps/web/app`, `features`, shared UI, API client và frontend test | Chốt luồng màn hình, trạng thái loading/error/empty, review API usability |
| **TV3 – Agent/Integration/DevOps** | Kết nối hệ thống và bảo đảm có thể chạy demo | Agent/Gateway simulator hoặc adapter, persistence, integration test, Docker/CI | Quản lý dữ liệu demo, quan sát log, kịch bản demo và release candidate |

Quy tắc tránh chia nhóm thành ba “ốc đảo”:

- Mỗi issue có một người thực hiện và ít nhất một người khác review.
- Khi một chức năng đi qua nhiều layer, nhóm chốt contract trước; từng người vẫn làm PR nhỏ theo phần mình sở hữu.
- TV1 không làm thay toàn bộ backend, TV2 không chỉ thiết kế giao diện, TV3 không chỉ viết tài liệu. Mỗi thành viên phải có code chạy được và được merge.
- Review luân phiên: `TV1 → TV2`, `TV2 → TV3`, `TV3 → TV1`; PR rủi ro cao được cả hai người còn lại xem.
- Một người phụ trách chính không đồng nghĩa là sở hữu độc quyền file; thay đổi chéo vùng phải báo trong issue/PR.

## 4. Pipeline làm việc với Git

### 4.1. Luồng bắt buộc cho mọi công việc

```text
Backlog
  → tạo issue có acceptance criteria
  → gán người thực hiện và reviewer
  → tạo branch từ main mới nhất
  → code + test cục bộ
  → commit và push branch
  → mở Pull Request
  → CI + code review
  → sửa theo review
  → squash merge vào main
  → kiểm tra main và đóng issue
```

Không push trực tiếp lên `main`. Không merge code của chính mình khi chưa có approval. Không để một branch kéo dài qua nhiều milestone.

### 4.2. Quy ước branch và commit

Tên branch:

```text
feat/<issue-id>-<mo-ta-ngan>
fix/<issue-id>-<mo-ta-ngan>
test/<issue-id>-<mo-ta-ngan>
docs/<issue-id>-<mo-ta-ngan>
chore/<issue-id>-<mo-ta-ngan>
```

Ví dụ: `feat/12-session-state-machine`, `feat/18-preflight-dashboard`.

Commit dùng dạng Conventional Commits:

```text
feat(api): add policy deployment use case
feat(web): show workstation readiness
test(domain): cover invalid session transitions
fix(agent): retry command acknowledgement
docs(demo): add presentation script
```

Các lệnh làm việc cơ bản:

```powershell
git switch main
git pull --ff-only
git switch -c feat/<issue-id>-<mo-ta-ngan>

# Sau khi code và chạy test
git add <cac-file-lien-quan>
git commit -m "feat(scope): mo ta thay doi"
git push -u origin feat/<issue-id>-<mo-ta-ngan>
```

Sau đó mở PR, gắn issue, gán reviewer và chỉ merge khi đạt Definition of Done.

### 4.3. Checklist Pull Request

Mỗi PR phải có:

- Mục tiêu và issue liên quan.
- Danh sách thay đổi chính; ảnh/video ngắn nếu thay đổi UI.
- Cách kiểm thử và kết quả thực tế.
- Ảnh hưởng tới API, database, cấu hình hoặc biến môi trường.
- Xác nhận không vi phạm hướng phụ thuộc Clean Architecture.
- PR nhỏ, một mục tiêu; ưu tiên dưới khoảng 400 dòng thay đổi thuần nếu có thể.

Reviewer kiểm tra:

- Acceptance criteria đã đạt.
- Luật nghiệp vụ nằm đúng `domain/application`, không lọt vào router/UI/repository.
- Có test cho happy path và lỗi quan trọng.
- Không commit secret, database cục bộ, `.env`, `node_modules`, `.next` hoặc cache.
- Tên và thông báo lỗi đủ rõ; không có code chết hay TODO chặn demo.

### 4.4. Definition of Done

Một issue chỉ được coi là hoàn thành khi:

- Code nằm đúng layer và đã tự review.
- Test mới đã được viết và toàn bộ kiểm tra liên quan đều pass.
- Branch đã push, PR đã được ít nhất một thành viên khác approve và merge vào `main`.
- API contract/tài liệu được cập nhật nếu hành vi thay đổi.
- `main` vẫn chạy được; chức năng có thể trình diễn hoặc được quan sát bằng test/log.
- Issue được đóng và không còn lỗi mức blocker/critical liên quan.

Các lệnh kiểm tra tối thiểu trước khi yêu cầu review:

```powershell
uv run ruff check apps/api
uv run pytest

cd apps/web
npm run typecheck
npm run build
```

## 5. Lộ trình từ khởi tạo đến demo

Thời lượng dưới đây là gợi ý cho 6 tuần. Nếu lịch ngắn hơn, có thể rút số ngày nhưng không bỏ cổng nghiệm thu của từng mốc.

### Mốc 0 – Khởi tạo và chốt phạm vi (2–3 ngày)

**Mục tiêu:** cả ba người chạy được cùng một codebase và hiểu một luồng demo duy nhất.

| TV1 | TV2 | TV3 |
|---|---|---|
| Chốt use case, state machine, entity và API contract bản đầu | Vẽ wireframe dashboard, chốt navigation và trạng thái UI | Chuẩn hóa Docker Compose, dữ liệu cục bộ, lệnh setup và CI cơ bản |
| Code skeleton/domain test đầu tiên và mở PR | Code layout/design tokens hoặc màn hình skeleton và mở PR | Code health check/CI/script bootstrap và mở PR |

**Cổng nghiệm thu:** clone mới có thể cài dependency, chạy API + web; CI chạy lint/test/build; ba thành viên đều có ít nhất một PR được merge.

### Mốc 1 – Nghiệp vụ ca thi nền tảng (Tuần 1)

**Mục tiêu:** tạo và xem được ca thi, state transition được bảo vệ bởi domain.

| TV1 | TV2 | TV3 |
|---|---|---|
| Code `ExamSession`, trạng thái, use case tạo/lấy ca thi, unit test | Code form tạo ca thi, trang chi tiết và hiển thị trạng thái | Code SQLite repository/Unit of Work, migration hoặc bootstrap schema, integration test |

**Cổng nghiệm thu:** người dùng tạo ca thi từ UI, refresh trang vẫn xem được dữ liệu; transition sai trả lỗi rõ ràng.

### Mốc 2 – Policy, Agent/Gateway và preflight (Tuần 2)

**Mục tiêu:** triển khai policy đến các máy và biết máy nào sẵn sàng trước giờ thi.

| TV1 | TV2 | TV3 |
|---|---|---|
| Code policy version/hash, deploy use case, ACK validation và preflight rules | Code màn hình policy, tiến độ ACK và bảng readiness theo workstation | Code Agent/Gateway simulator: poll command, apply/ACK, gửi preflight; thêm integration test |

**Cổng nghiệm thu:** demo được `Deploy → ACK → Preflight`; policy hash sai bị từ chối; UI chỉ rõ `READY/WARNING/FAILED`.

### Mốc 3 – Vận hành ca thi và phát hiện sự cố (Tuần 3)

**Mục tiêu:** bắt đầu ca thi, nhận telemetry và tạo incident có bằng chứng.

| TV1 | TV2 | TV3 |
|---|---|---|
| Code điều kiện start/force-start, telemetry use case và incident policy | Code live dashboard, danh sách sự kiện/sự cố, filter và trạng thái lỗi kết nối | Code telemetry generator/simulator, correlation test và log phục vụ chẩn đoán |

**Cổng nghiệm thu:** ca chưa đạt điều kiện không thể start thường; force-start phải có lý do; ba lỗi DNS tương quan tạo đúng một incident và không coi mọi blocked event là gian lận.

### Mốc 4 – Kết thúc, khôi phục và báo cáo (Tuần 4)

**Mục tiêu:** đóng được vòng đời ca thi và tạo bằng chứng sau ca.

| TV1 | TV2 | TV3 |
|---|---|---|
| Code finish/restore, summary và kiểm tra audit hash-chain | Code màn hình summary, chỉ số readiness/incident/audit và trạng thái restore | Code ACK khôi phục, dữ liệu demo lặp lại được, integration test toàn pipeline |

**Cổng nghiệm thu:** `Finish → Restore → Summary` chạy được; summary thể hiện incident, telemetry, audit và kết quả kiểm tra hash-chain.

### Mốc 5 – Tích hợp và hardening (Tuần 5)

**Mục tiêu:** biến các phần riêng lẻ thành một MVP ổn định.

| TV1 | TV2 | TV3 |
|---|---|---|
| Rà soát invariant, lỗi nghiệp vụ, API contract và test coverage quan trọng | Hoàn thiện responsive/accessibility, loading/error/empty states và luồng thao tác | Chạy end-to-end, sửa cấu hình Docker/CI, backup/reset dữ liệu demo và kiểm tra log |

Cả nhóm cùng thực hiện bug bash. Mỗi lỗi có issue, branch và PR như chức năng bình thường; không sửa nóng trực tiếp trên `main`.

**Cổng nghiệm thu:** một máy mới chạy được bằng README; toàn bộ checks pass; không còn lỗi blocker/critical; kịch bản demo chạy liên tục ít nhất 3 lần.

### Mốc 6 – Release candidate và demo (Tuần 6)

**Mục tiêu:** đóng băng phạm vi, diễn tập và trình bày sản phẩm có bằng chứng kỹ thuật.

| TV1 | TV2 | TV3 |
|---|---|---|
| Trình bày bài toán, nghiệp vụ, state machine và Clean Architecture | Điều khiển dashboard, trình bày trải nghiệm và kết quả ca thi | Chuẩn bị môi trường, chạy simulator, trình bày tích hợp/CI và phương án dự phòng |

Việc chung:

- Tạo tag release candidate, ví dụ `v0.1.0-rc.1`, từ commit `main` đã qua kiểm thử.
- Chuẩn bị dữ liệu cố định gồm gateway, ít nhất 3 workstation và  policy mẫu.
- Ghi lại video dự phòng và chụp ảnh các màn hình chính.
- Không thêm tính năng mới trong 48 giờ trước demo; chỉ nhận bug fix có PR và review.
- Diễn tập đúng thời lượng, thống nhất ai nói, ai thao tác và ai xử lý sự cố.

**Cổng nghiệm thu cuối:** bản release chạy được bằng Docker Compose, kịch bản demo hoàn tất, kiểm tra tự động pass và cả ba thành viên đều có lịch sử code/PR/merge rõ ràng.

## 6. Kịch bản demo chuẩn

| Bước | Người thao tác | Kết quả cần cho thấy |
|---|---|---|
| 1. Khởi chạy hệ thống | TV3 | API, web và database healthy |
| 2. Tạo ca thi và danh sách máy | TV2 | Session ở trạng thái `CREATED` |
| 3. Chọn và deploy policy | TV2 | Policy có version/hash; trạng thái chuyển sang triển khai |
| 4. Agent/Gateway nhận lệnh và ACK | TV3 | Từng target ACK đúng policy hash |
| 5. Gửi kết quả preflight | TV3 | Dashboard cập nhật `READY/WARNING/FAILED` |
| 6. Bắt đầu ca thi | TV2 | Domain chỉ cho phép transition hợp lệ; có thể giải thích force-start |
| 7. Phát telemetry mô phỏng lỗi DNS | TV3 | Sự kiện xuất hiện và incident được gom đúng policy |
| 8. Giải thích kiến trúc | TV1 | Chỉ rõ rule nằm ở domain/use case, adapter có thể thay thế |
| 9. Kết thúc và restore | TV2 + TV3 | Các target nhận lệnh khôi phục và trở về trạng thái bình thường |
| 10. Xem summary/audit | TV1 | Thấy thống kê, incident, số audit event và hash-chain hợp lệ |

Phương án dự phòng: nếu simulator hoặc mạng lỗi, dùng script demo đã kiểm tra trước; nếu UI lỗi, gọi API qua Swagger; nếu máy trình chiếu lỗi, dùng video quay từ đúng release candidate. Phương án dự phòng không thay thế việc sửa lỗi trước ngày demo.

## 7. Nhịp phối hợp hằng ngày

- Đầu ngày: họp tối đa 15 phút, mỗi người nói việc đã xong, việc hôm nay và blocker.
- Trước khi code: issue phải có phạm vi, acceptance criteria, owner và reviewer.
- Cuối ngày: mọi code đang làm phải được commit/push lên branch cá nhân; không giữ thay đổi quan trọng chỉ ở máy local.
- PR nên được review trong vòng một ngày làm việc. Blocker báo ngay trong nhóm, không chờ tới buổi họp tiếp theo.
- Cuối tuần/milestone: demo trên `main`, cập nhật backlog và chốt phạm vi mốc sau.

## 8. Bảng theo dõi đóng góp

Dùng bảng này trong từng milestone để bảo đảm cả ba người đều trực tiếp đưa code vào dự án:

| Milestone | Thành viên | Issue | Branch | PR | Reviewer | CI | Đã merge |
|---|---|---|---|---|---|---|---|
| M0 | TV1 |  |  |  | TV2 | ⬜ | ⬜ |
| M0 | TV2 |  |  |  | TV3 | ⬜ | ⬜ |
| M0 | TV3 |  |  |  | TV1 | ⬜ | ⬜ |

Sao chép ba dòng trên cho mỗi milestone. Một milestone chưa hoàn tất nếu còn thành viên chưa có PR code được merge, trừ trường hợp nhóm ghi rõ lý do và bù ở milestone kế tiếp.

## 9. Checklist trước ngày demo

- [ ] Đã chốt commit/tag sẽ demo và không chạy từ branch cá nhân.
- [ ] `ruff`, backend test, frontend typecheck/build và end-to-end test đều pass.
- [ ] Docker Compose chạy được từ môi trường sạch.
- [ ] Dữ liệu demo có thể reset và tạo lại trong vài phút.
- [ ] Không có secret hoặc tài khoản thật trong repository/màn hình.
- [ ] Kịch bản chính và các tình huống lỗi đã được diễn tập.
- [ ] Mỗi thành viên biết phần trình bày và phần code của mình.
- [ ] Có video, ảnh và script/API dự phòng.
- [ ] README và sơ đồ Clean Architecture khớp với code thực tế.
- [ ] Cả ba thành viên có issue, commit, PR review và code đã merge trên lịch sử Git.
