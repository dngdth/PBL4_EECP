# Local Gateway trên cùng máy server — phòng thi 30 máy

## 1. Topology

Một máy server chạy Backend, Web, PostgreSQL, Redis và Local Gateway. Mỗi máy
thi chạy Agent Client bằng tài khoản thường và Agent Service riêng dưới LocalSystem.
Không cần dành một máy thi để chạy Gateway.

```text
30 Agent Client ── WSS :8443 ── Local Gateway
                                     │
                              WSS, mạng nội bộ Docker
                                     │
                         gateway-backend-tls :9443
                                     │ HTTP nội bộ Docker
                                API :8000
                                     │
                              PostgreSQL / Redis
```

`gateway-backend-tls` chỉ kết thúc TLS cho uplink; Local Gateway vẫn là ứng dụng
định tuyến trong `apps/gateway`. Endpoint TLS nội bộ không publish thêm cổng host.
Không dùng `localhost` để chỉ container API từ container Gateway.

## 2. Ranh giới kiến trúc

- `apps/gateway/app/application`: routing, Event delivery, presence monitor và ports.
  Không phụ thuộc FastAPI, WebSocket library hoặc SQLite adapter.
- `agent_ws.py`: adapter nhận WebSocket và xác thực identity của Agent.
- `backend_client.py`: adapter WSS uplink, reconnect, hàng đợi có giới hạn và health.
- `event_buffer.py`: adapter SQLite durable Event store.
- `main.py`: composition root kết nối ports với adapter và quản lý lifecycle.
- Backend application use case `RecordGatewayDeliveryFailure` xác nhận Gateway,
  Agent binding và target/correlation của command; ghi audit và giữ retry/deadline.
  Gateway không tạo session/policy, ký command hoặc thay đổi Windows.

## 3. Chuẩn bị cấu hình Docker trên server

Copy `.env.example` thành `.env`, thay toàn bộ placeholder bằng giá trị thật.
Giữ nguyên các signing secret ở Backend và Agent Service; không cung cấp cho
Gateway hoặc Agent Client.

Các thiết lập Gateway cần khớp:

```dotenv
EECP_GATEWAY_ID=GW-A
EECP_GATEWAY_ROOM_ID=LAB-A
EECP_GATEWAY_BACKEND_WS_URL=wss://gateway-backend-tls:9443/api/v2/gateways/ws
EECP_GATEWAY_TLS_CERTFILE_HOST=./local-secrets/gateway.crt
EECP_GATEWAY_TLS_KEYFILE_HOST=./local-secrets/gateway.key
EECP_GATEWAY_BACKEND_CA_FILE_HOST=./local-secrets/ca.crt
```

Certificate `gateway.crt` phải được CA tin cậy ký và có Subject Alternative Name:

- DNS `gateway-backend-tls` cho kết nối giữa hai container.
- DNS hoặc IP của máy server mà Agent dùng, ví dụ IP `192.168.3.50`.

Gateway dùng `ca.crt` để kiểm tra certificate Backend, Agent dùng cùng CA để kiểm
tra certificate Gateway. Không tắt kiểm tra TLS. `.crt` có thể chứa certificate
chain; không copy private key hoặc signing secret sang Agent Client.

`EECP_GATEWAY_BOOTSTRAP_TOKEN` là credential Gateway gửi tới Backend. Giá trị
SHA-256 của credential đó phải khớp record `GW-A` trong
`EECP_GATEWAY_CREDENTIALS_JSON`. Dù tên biến còn dùng "bootstrap", khi đã có
credential map thì Backend xác thực theo identity của Gateway.

Mỗi Agent có credential riêng. Credential PC01 phải khớp record PC01 trong
`EECP_AGENT_CREDENTIALS_JSON`; làm tương tự PC02–PC30. SHA-256 chỉ là giá trị
lưu trong map, Agent phải gửi secret gốc. Biến `EECP_AGENT_GATEWAY_TOKEN` trên
server vẫn được yêu cầu để tương thích cấu hình, nhưng production-like dùng
map theo từng Agent để xác thực.

Khởi động từ root repository:

```powershell
docker compose -f compose.yaml -f compose.gateway-server.yaml --profile gateway config --quiet
docker compose -f compose.yaml -f compose.gateway-server.yaml --profile gateway up -d --build
```

Mở TCP 8443 từ mạng phòng thi tới server. Giới hạn phạm vi firewall theo subnet
thực tế của phòng. Web/API giữ cấu hình triển khai riêng trong Compose gốc.
Volume `gateway-buffer` giữ Event qua restart/recreate; không xóa volume này
khi vẫn còn Event chưa gửi.

## 4. Cấu hình Agent trên máy thi

Ví dụ cho PC01, server IP `192.168.3.50`:

```powershell
$env:EECP_AGENT_ID="PC01"
$env:EECP_GATEWAY_URL="wss://192.168.3.50:8443/ws/agents"
$env:EECP_GATEWAY_CA_FILE="C:\ProgramData\EECP\certs\ca.crt"
# EECP_AGENT_GATEWAY_TOKEN: secret riêng PC01, cấp qua cấu hình được bảo vệ.
uv run python -m agent.client.main
```

Đặt `EECP_GATEWAY_URL` giống nhau cho Client và Service để Service bảo vệ endpoint
Gateway khỏi policy Firewall tự chặn đường điều khiển. Agent Service phải được
cài/chạy riêng theo `final-demo-guide.md`; không chạy Client bằng Administrator
để thay thế Service. Không dùng `--direct-backend-compat` cho luồng này.

## 5. Chạy native cùng server

Gateway cũng chạy được bằng `uv run python -m apps.gateway.app.main` ngoài Docker.
Cấu hình `EECP_GATEWAY_BACKEND_WS_URL` tới endpoint Backend WSS thực sự, ví dụ
`wss://server-phong-thi:9443/api/v2/gateways/ws`, và
`EECP_GATEWAY_BACKEND_CA_FILE` tới CA trên host. Native Backend cần TLS hoặc reverse
proxy TLS được cấu hình riêng; Uvicorn HTTP trên port 8000 không tự trở thành WSS.
Docker override ở trên là đường triển khai cùng server đã chuẩn bị trong repository.

## 6. Kiểm tra phục hồi trong phòng thi

1. Cho PC01–PC30 kết nối, xác nhận đủ 30 Agent và identity/room của Gateway.
2. Gửi policy tới một máy; xác nhận chỉ máy đó nhận và ACK, applied hash đúng.
3. Cho một Agent offline rồi gửi command: Backend ghi `COMMAND_DELIVERY_FAILED`,
   chưa ghi ACK thành công/thất bại thực thi. Kết nối lại trước retry/deadline;
   cùng command được retry, Service chống thực thi trùng.
4. Dùng cùng các đối số Compose để dừng riêng API bằng `stop api`. Giữ Gateway
   chạy, tạo Event và kiểm tra pending Event tăng trong SQLite/health Gateway.
   Dashboard không thể cập nhật trong lúc API dừng.
5. `start api`: Gateway tự reconnect, công bố lại identity và presence hiện tại,
   gửi lại Event cho tới receipt. Backend dedupe, backlog về 0.
6. Restart riêng `local-gateway` với volume còn nguyên: Agent tự reconnect và
   pending Event tiếp tục được gửi. Kiểm tra không sinh incident trùng.
7. Khi có nhiều message, health vẫn gửi định kỳ. Khi hàng đợi control đầy,
   presence monitor tiếp tục chạy và retry trạng thái mới nhất khi có chỗ.

## 7. Phạm vi bảo đảm

Event đã commit vào SQLite được retry cho tới receipt thành công hoặc terminal
rejection, với retry có backoff. Receipt thành công mới xóa Event. Terminal
rejection được giữ để kiểm tra; buffer đầy từ chối Event mới và báo lỗi.

Gateway/Backend là tiến trình riêng trên cùng server: restart riêng Backend có
thể giữ kết nối LAN và thu Event tại Gateway. Toàn bộ server tắt, mất điện hoặc
LAN đứt thì Gateway không nhận được Event mới. Agent chưa có durable Event queue
trước Gateway. ACK/control queue vẫn là RAM; retry command phía Backend và replay
protection phía Service là cơ chế phục hồi, không phải cam kết exactly-once.

Gateway chỉ giữ identity/presence hiện tại và bộ đệm Event. Không có HA hoặc dự
phòng phần cứng. Kiểm thử loopback/WSS không thay thế kiểm tra 30 máy Windows thật.

## 8. Regression đã bổ sung

`test_gateway_same_server.py` mở WSS thực trên loopback với certificate test và
credential từng máy, kết nối 30 Agent giả lập, gửi lệnh đúng target, nhận ACK,
dừng/khởi tạo lại Backend từ database và kiểm tra socket LAN còn kết nối. Test
cũng khởi tạo lại Gateway từ SQLite cũ, kết nối lại Agent và xác nhận đủ Event,
không nhân đôi incident. Certificate không tin cậy bị từ chối.

Các test riêng kiểm tra health khi có traffic liên tục, gửi lại message bị lỗi
trước khi hoàn tất send, presence qua backpressure, stale socket, handshake timeout,
delivery failure giả mạo và command retry thành công sau khi Agent offline.
Architecture test ngăn application layer phụ thuộc adapter WebSocket/SQLite.

```powershell
uv run pytest apps/api/test/unit/test_gateway_resilience.py apps/api/test/integration/test_gateway_delivery_failure.py apps/api/test/integration/test_gateway_same_server.py
```

Test WSS cần `openssl` trong PATH; nếu thiếu sẽ được báo skipped. Test tự tạo
certificate trong thư mục tạm, không đọc hoặc thay certificate triển khai.
