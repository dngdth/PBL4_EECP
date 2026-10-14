# HƯỚNG DẪN TEST DEMO HỆ THỐNG EECP

> **Luồng hiện tại: Gateway chạy trên chính máy server.** Xem
> [hướng dẫn Gateway trên server cho phòng 30 máy](docs/v2/gateway-on-server.md)
> để cấu hình WSS, credential, Agent Client/Service và kiểm tra phục hồi.
> Các phần bên dưới là hướng dẫn legacy qua `agent.main`, không phải quy trình
> triển khai Gateway/Named Pipe hiện tại.

## Tổng quan
Hướng dẫn này giúp bạn test hệ thống EECP với backend, frontend và agent trên nhiều máy trong mạng LAN. Mục tiêu chính là kiểm tra xem hệ thống có phát hiện và report được khi máy client truy cập vào ChatGPT hay không.

## Yêu cầu

### Phần cứng
- **Máy Server** (Control Server): 1 máy - IP đề xuất: `192.168.3.50`
- **Máy Client** (Workstation): Ít nhất 2 máy - IP đề xuất: `192.168.3.56`, `192.168.3.55`
- Tất cả các máy phải trong cùng mạng LAN

### Phần mềm
- Python 3.12+ (đã cài đặt `uv`)
- Node.js 18+ và npm
- PowerShell (Windows)
- Git (để clone repo)

---

## PHẦN 1: SETUP MÁY SERVER (Control Server)

### Bước 1.1: Chuẩn bị dự án
```powershell
# Mở PowerShell tại thư mục dự án
cd C:\github\PBL4_EECP

# Sync dependencies
uv sync --all-packages
```

### Bước 1.2: Kiểm tra địa chỉ IP của Server
```powershell
# Kiểm tra IP của máy server
ipconfig

# Tìm IPv4 Address trong phần Ethernet adapter hoặc WiFi
# Ví dụ: 192.168.3.50
# Ghi nhớ IP này để cấu hình ở các bước sau
```

### Bước 1.3: Cấu hình Windows Firewall
```powershell
# Mở Windows Firewall và cho phép port 8000 (API) và 3000 (Web)
# Hoặc chạy lệnh sau trong PowerShell với quyền Administrator:

New-NetFirewallRule -DisplayName "EECP API" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
New-NetFirewallRule -DisplayName "EECP Web" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow
```

### Bước 1.4: Chạy Backend API
```powershell
# Terminal 1: Chạy backend
# Thay 192.168.3.50 bằng IP thực của máy server nếu khác
uv run --package eecp-api uvicorn app.main:app --app-dir apps/api --host 0.0.0.0 --port 8000
```

**Kiểm tra:**
- Mở trình duyệt: `http://localhost:8000/docs` (trên server)
- Hoặc: `http://192.168.3.50:8000/docs` (từ máy khác trong mạng)
- Bạn sẽ thấy Swagger UI của FastAPI

### Bước 1.5: Chạy Frontend Web
```powershell
# Terminal 2: Chạy frontend (mở PowerShell mới)
$env:EECP_API_URL="http://192.168.3.50:8000"
Set-Location apps/web
npm install
npm run dev -- --hostname 0.0.0.0
```

**Kiểm tra:**
- Mở trình duyệt: `http://localhost:3000` (trên server)
- Hoặc: `http://192.168.3.50:3000` (từ máy khác)
- Bạn sẽ thấy giao diện web EECP

---

## PHẦN 2: SETUP MÁY CLIENT (Workstation)

### Bước 2.1: Clone hoặc Copy dự án
```powershell
# Trên máy Client, clone dự án
git clone <repository-url> C:\github\PBL4_EECP
cd C:\github\PBL4_EECP

# Hoặc copy toàn bộ thư mục từ máy server sang
```

### Bước 2.2: Cài đặt Python dependencies
```powershell
# Chỉ cần cài agent dependencies
pip install -r requirements.txt
# Hoặc nếu dùng uv:
uv sync
```

### Bước 2.3: Kiểm tra IP của máy Client
```powershell
ipconfig
# Ghi nhớ IPv4 Address
# Ví dụ: PC01 = 192.168.3.56, PC02 = 192.168.3.55
```

---

## PHẦN 3: TEST DEMO - AGENT REGISTRATION & HEARTBEAT

### Bước 3.1: Chạy Agent trên PC01
```powershell
# Trên máy Client thứ nhất (PC01)
# Mở PowerShell tại thư mục dự án
cd C:\github\PBL4_EECP

$env:EECP_SERVER_URL="http://192.168.3.50:8000"
$env:EECP_AGENT_ID="PC01"
$env:EECP_POLICY_MODE="audit"
Write-Host "Agent ID: $env:EECP_AGENT_ID"
python -m agent.main
```

**Kết quả mong đợi:**
- Terminal hiển thị: "Agent PC01 registered" và bắt đầu heartbeat
- Không có lỗi kết nối

### Bước 3.2: Chạy Agent trên PC02
```powershell
# Trên máy Client thứ hai (PC02)
cd C:\github\PBL4_EECP

$env:EECP_SERVER_URL="http://192.168.3.50:8000"
$env:EECP_AGENT_ID="PC02"
$env:EECP_POLICY_MODE="audit"
Write-Host "Agent ID: $env:EECP_AGENT_ID"
python -m agent.main
```

### Bước 3.3: Kiểm tra trên Dashboard
1. Mở trình duyệt: `http://192.168.3.50:3000/workstations`
2. **Kiểm tra:**
   - PC01 hiển thị trạng thái `ONLINE`
   - PC02 hiển thị trạng thái `ONLINE`
   - Xem thông tin: IP, hostname, OS version

### Bước 3.4: Test Offline Detection
1. Trên một máy Client (ví dụ PC01), nhấn `Ctrl+C` để dừng agent
2. Đợi 15-20 giây
3. Refresh dashboard: `http://192.168.3.50:3000/workstations`
4. **Kiểm tra:**
   - PC01 hiển thị `OFFLINE`
   - PC02 vẫn `ONLINE`
5. Chạy lại agent trên PC01 và kiểm tra nó quay về `ONLINE`

---

## PHẦN 4: TEST DEMO - EXAM SESSION MANAGEMENT

### Bước 4.1: Đảm bảo tất cả Agent đang ONLINE
- Kiểm tra `http://192.168.3.50:3000/workstations`
- PC01 và PC02 đều phải `ONLINE`

### Bước 4.2: Tạo Exam Session qua Web UI
1. Mở: `http://192.168.3.50:3000/sessions/create`
2. Điền thông tin:
   - **Exam Name**: "Test Demo"
   - **Room**: "A101"
   - **Select Workstations**: Chọn PC01 và PC02
3. Nhấn **Create Session**

### Bước 4.3: Kiểm tra Session Dashboard
1. Mở: `http://192.168.3.50:3000/sessions`
2. **Kiểm tra:**
   - Session "Test Demo" hiển thị với status `CREATED`
   - Workstations được assign: PC01, PC02
   - Policy status cho mỗi workstation: `POLICY PENDING` → `POLICY APPLIED`

### Bước 4.4: Chuyển trạng thái Session
1. Đợi đến khi cả PC01 và PC02 đều có policy status = `POLICY APPLIED`
2. Nhấn nút **Transition to READY**
3. **Kiểm tra:** Status chuyển sang `READY`
4. Nhấn nút **Transition to RUNNING**
5. **Kiểm tra:** Status chuyển sang `RUNNING`

---

## PHẦN 5: TEST POLICY ENFORCEMENT - PHÁT HIỆN TRUY CẬP CHATGPT

### Bước 5.1: Tạo Custom Policy Profile chặn AI
1. Mở: `http://192.168.3.50:3000/policies`
2. Nhấn **Create New Profile**
3. Điền thông tin:
   - **Name**: "Block AI Sites"
   - **Description**: "Block all AI and ChatGPT access"
4. **Network Categories**: Chọn `AI_AND_CHATBOTS` (để chặn)
5. **Denied Applications**: Thêm các ứng dụng AI nếu cần
6. **USB Storage**: Chọn `DENY`
7. Nhấn **Save**

### Bước 5.2: Chạy Agent ở chế độ ENFORCE (Quan trọng!)
**LƯU Ý:** Chế độ này sẽ thay đổi cài đặt Windows thật, cần chạy PowerShell với quyền **Administrator**

```powershell
# Trên PC01 - MỞ POWERSHELL VỚI QUYỀN ADMINISTRATOR
cd C:\github\PBL4_EECP

$env:EECP_SERVER_URL="http://192.168.3.50:8000"
$env:EECP_AGENT_ID="PC01"
$env:EECP_POLICY_MODE="enforce"
python -m agent.main
```

### Bước 5.3: Tạo Session mới với Policy "Block AI Sites"
1. Finish session cũ nếu đang chạy: `http://192.168.3.50:3000/sessions`
2. Tạo session mới: `http://192.168.3.50:3000/sessions/create`
3. **Policy Profile**: Chọn "Block AI Sites" (custom profile vừa tạo)
4. Select workstations: PC01 (đang chạy ở chế độ enforce)
5. Create session

### Bước 5.4: Transition Session to RUNNING
1. Đợi policy status = `POLICY APPLIED`
2. Transition: `CREATED` → `READY` → `RUNNING`

### Bước 5.5: Test truy cập ChatGPT trên PC01
1. **Trên PC01**, mở trình duyệt
2. Thử truy cập: `https://chat.openai.com` hoặc `https://chatgpt.com`
3. **Kết quả mong đợi:**
   - Trang web **KHÔNG TẢI ĐƯỢC** (bị chặn bởi hosts file)
   - Trình duyệt hiển thị lỗi "Cannot resolve hostname" hoặc tương tự

### Bước 5.6: Kiểm tra Backend Logs và Telemetry
```powershell
# Trên Server, kiểm tra terminal backend (Terminal 1)
# Bạn sẽ thấy logs về:
# - Policy được apply thành công
# - Agent gửi heartbeat
# - (Nếu có) Telemetry events từ agent

# Kiểm tra qua API
# Mở: http://192.168.3.50:8000/docs
# Thử endpoint: GET /api/v1/sessions/{session_id}
# Xem chi tiết session và policy status
```

### Bước 5.7: Kiểm tra Hosts File trên PC01 (Tùy chọn)
```powershell
# Trên PC01, mở PowerShell
notepad C:\Windows\System32\drivers\etc\hosts

# Tìm dòng có comment "# EECP Policy Block"
# Bạn sẽ thấy các domain AI bị chặn, ví dụ:
# 0.0.0.0 chat.openai.com  # EECP Policy Block
# 0.0.0.0 chatgpt.com       # EECP Policy Block
```

---

## PHẦN 6: TEST RESTORE POLICY

### Bước 6.1: Finish Session
1. Mở: `http://192.168.3.50:3000/sessions`
2. Nhấn **Transition to FINISHED**
3. **Kiểm tra:** Status chuyển sang `FINISHED`

### Bước 6.2: Agent nhận lệnh RESTORE
- Kiểm tra terminal agent trên PC01
- Bạn sẽ thấy log: "Processing RESTORE_BASELINE command"
- Agent sẽ:
  - Xóa các dòng EECP block trong hosts file
  - Restore USB setting
  - Xóa policy state file

### Bước 6.3: Kiểm tra truy cập ChatGPT lại
1. **Trên PC01**, mở trình duyệt
2. Thử truy cập: `https://chat.openai.com`
3. **Kết quả mong đợi:**
   - Trang web **TẢI ĐƯỢC BÌNH THƯỜNG**
   - Policy đã được restore

### Bước 6.4: Kiểm tra Hosts File đã clean (Tùy chọn)
```powershell
# Trên PC01
notepad C:\Windows\System32\drivers\etc\hosts

# Kiểm tra: Không còn dòng "# EECP Policy Block"
```

---

## PHẦN 7: TEST TỪ MÁY KHÁC TRONG MẠNG

### Bước 7.1: Chuẩn bị máy test thứ 3 (không cài agent)
- Máy này chỉ dùng để truy cập web UI và kiểm tra hệ thống
- IP ví dụ: `192.168.3.100`

### Bước 7.2: Truy cập Dashboard từ máy thứ 3
```
Mở trình duyệt và truy cập:
- Dashboard: http://192.168.3.50:3000
- Workstations: http://192.168.3.50:3000/workstations
- Sessions: http://192.168.3.50:3000/sessions
- API Docs: http://192.168.3.50:8000/docs
```

### Bước 7.3: Tạo và quản lý Session từ máy thứ 3
1. Tạo session mới từ máy thứ 3
2. Monitor real-time status của PC01, PC02
3. Transition session từ máy thứ 3
4. **Kiểm tra:** Tất cả thao tác hoạt động bình thường như từ server

---

## PHẦN 8: TEST API BẰNG POWERSHELL (Optional)

### Bước 8.1: Test tạo Session qua API
```powershell
# Chạy trên bất kỳ máy nào trong mạng
$apiBase="http://192.168.3.50:8000"
$createBody=@{
  name="API Test Session"
  room="B202"
  agent_ids=@("PC01", "PC02")
  actor="teacher"
} | ConvertTo-Json

$session=Invoke-RestMethod -Method Post -Uri "$apiBase/api/v1/sessions" -ContentType "application/json" -Body $createBody
Write-Host "Created session: $($session.id)"
```

### Bước 8.2: List tất cả Sessions
```powershell
Invoke-RestMethod -Method Get -Uri "$apiBase/api/v1/sessions"
```

### Bước 8.3: Get chi tiết Session
```powershell
$sessionId = $session.id
Invoke-RestMethod -Method Get -Uri "$apiBase/api/v1/sessions/$sessionId"
```

### Bước 8.4: Transition Session lifecycle
```powershell
# Đợi policy applied, sau đó:
foreach ($status in "READY", "RUNNING", "FINISHED") {
  Write-Host "Transitioning to $status"
  $statusBody=@{ status=$status; actor="teacher" } | ConvertTo-Json
  Invoke-RestMethod -Method Patch -Uri "$apiBase/api/v1/sessions/$sessionId/status" -ContentType "application/json" -Body $statusBody
  Start-Sleep -Seconds 2
}
```

---

## CHECKLIST KIỂM TRA TỔNG THỂ

### ✅ Backend & Frontend
- [ ] Backend API chạy thành công tại `http://192.168.3.50:8000`
- [ ] Frontend web chạy thành công tại `http://192.168.3.50:3000`
- [ ] API docs truy cập được từ máy khác: `http://192.168.3.50:8000/docs`

### ✅ Agent Registration
- [ ] PC01 đăng ký thành công và hiển thị `ONLINE`
- [ ] PC02 đăng ký thành công và hiển thị `ONLINE`
- [ ] Agent offline detection hoạt động (hiển thị `OFFLINE` sau 15-20s)
- [ ] Agent reconnect thành công không tạo duplicate

### ✅ Session Management
- [ ] Tạo session thành công qua Web UI
- [ ] Session hiển thị đúng workstations được assign
- [ ] Policy được deploy và status chuyển sang `POLICY APPLIED`
- [ ] Transition lifecycle: `CREATED` → `READY` → `RUNNING` → `FINISHED`

### ✅ Policy Enforcement (chế độ enforce)
- [ ] Agent chạy ở chế độ `enforce` với quyền Administrator
- [ ] Custom policy profile tạo thành công
- [ ] Session sử dụng custom policy thành công
- [ ] **ChatGPT bị chặn** khi session RUNNING (không truy cập được)
- [ ] Hosts file có dòng `# EECP Policy Block`
- [ ] **ChatGPT truy cập được** sau khi session FINISHED (policy restored)
- [ ] Hosts file đã clean sau restore

### ✅ Multi-machine Access
- [ ] Dashboard truy cập được từ máy khác trong LAN
- [ ] Tạo và quản lý session từ máy khác thành công
- [ ] API endpoints truy cập được từ máy khác

### ✅ API Testing
- [ ] Tạo session qua PowerShell API thành công
- [ ] Transition session qua API thành công
- [ ] Get session details qua API thành công

---

## TROUBLESHOOTING

### Lỗi: Agent không kết nối được Server
```
ConnectionError: Cannot reach http://192.168.3.50:8000
```
**Giải pháp:**
1. Kiểm tra backend có đang chạy không: `http://192.168.3.50:8000/health`
2. Kiểm tra firewall: Đảm bảo port 8000 được mở
3. Ping server từ client: `ping 192.168.3.50`

### Lỗi: Frontend không load API
```
API Error: Failed to fetch
```
**Giải pháp:**
1. Kiểm tra biến môi trường: `$env:EECP_API_URL` phải đúng
2. Restart frontend sau khi set biến môi trường

### Lỗi: Agent không thể enforce policy
```
PermissionError: Access denied
```
**Giải pháp:**
1. Mở PowerShell với quyền **Run as Administrator**
2. Đảm bảo `EECP_POLICY_MODE="enforce"`

### Lỗi: ChatGPT vẫn truy cập được dù policy đã applied
**Giải pháp:**
1. Kiểm tra agent có chạy ở chế độ `enforce` không (không phải `audit`)
2. Kiểm tra hosts file: `notepad C:\Windows\System32\drivers\etc\hosts`
3. Clear DNS cache: `ipconfig /flushdns`
4. Restart trình duyệt

### Lỗi: Policy không restore sau khi finish session
**Giải pháp:**
1. Kiểm tra agent logs, tìm "RESTORE_BASELINE"
2. Manually restore:
   - Mở hosts file với quyền admin
   - Xóa các dòng có `# EECP Policy Block`
   - Save file

---

## KẾT LUẬN

Sau khi hoàn thành tất cả các bước test trên, bạn đã xác minh được:

1. **Hệ thống hoạt động trong mạng LAN** với nhiều máy client
2. **Policy enforcement thực sự chặn truy cập ChatGPT** (và các AI sites khác)
3. **Hệ thống phát hiện và report** trạng thái policy trên dashboard
4. **Restore policy thành công** sau khi kết thúc session
5. **Multi-machine access** hoạt động tốt cho teacher/admin

Hệ thống EECP đã sẵn sàng để demo hoặc triển khai thực tế trong môi trường thi!
