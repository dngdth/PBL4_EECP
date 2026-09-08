export class ApiError extends Error {
  public statusCode: number;
  public details?: unknown;

  constructor(message: string, statusCode: number = 500, details?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.statusCode = statusCode;
    this.details = details;
  }
}

export function normalizeError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error;
  }
  if (error instanceof Error) {
    return new ApiError(error.message, 500);
  }
  return new ApiError('Đã xảy ra lỗi không mong muốn.', 500);
}

/**
 * Translates backend domain exception messages and status codes
 * into clear, user-friendly, actionable Vietnamese explanations.
 */
export function formatApiErrorMessage(error: unknown): string {
  if (!error) return 'Đã xảy ra lỗi không xác định.';

  const rawMsg =
    error instanceof ApiError || error instanceof Error
      ? error.message
      : typeof error === 'string'
        ? error
        : JSON.stringify(error);

  const lower = rawMsg.toLowerCase();

  if (lower.includes('management transitions are unavailable for pipeline sessions')) {
    return 'Ca thi này có Gateway quản trị phòng máy. Quy trình yêu cầu chuyển trạng thái tự động qua các nút: [Triển khai chính sách] ➔ [Bắt đầu thi] ➔ [Kết thúc ca thi], không thể đổi trạng thái thủ công.';
  }

  if (lower.includes('cannot transition management session')) {
    return `Không thể chuyển trạng thái ca thi theo quy tắc nghiệp vụ (${rawMsg}).`;
  }

  if (lower.includes('policy_in_use') || lower.includes('policy in use')) {
    return 'Chính sách này đang được liên kết với ca thi khác trong hệ thống, không thể xóa để bảo toàn lịch sử.';
  }

  if (lower.includes('duplicate') && lower.includes('policy')) {
    return 'Mã hồ sơ chính sách bảo mật đã tồn tại trên hệ thống. Vui lòng chọn mã khác.';
  }

  if (lower.includes('exam name is required') || lower.includes('name is required')) {
    return 'Tên ca thi là bắt buộc và không được để trống.';
  }

  if (lower.includes('room') && lower.includes('required')) {
    return 'Phòng thi là bắt buộc và không được để trống.';
  }

  if (lower.includes('not found') || lower.includes('404')) {
    return 'Dữ liệu yêu cầu không tồn tại hoặc đã bị xóa khỏi hệ thống.';
  }

  if (lower.includes('timed out') || lower.includes('timeout')) {
    return 'Yêu cầu tới máy chủ bị quá thời gian (Timeout). Vui lòng kiểm tra kết nối mạng.';
  }

  if (lower.includes('failed to fetch') || lower.includes('network error')) {
    return 'Không thể kết nối đến máy chủ Backend (FastAPI). Vui lòng kiểm tra xem server đã bật chưa.';
  }

  return rawMsg;
}
