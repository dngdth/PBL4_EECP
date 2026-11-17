import React, { useCallback, useEffect, useState } from 'react';
import { AlertCircle, Network, RefreshCw, Router, Server } from 'lucide-react';
import { listGateways, GatewayOperationalState } from '@/src/features/gateways/services/gatewayApi';
import { Button } from '@/src/shared/ui/button';
import { Card } from '@/src/shared/ui/card';
import { Badge } from '@/src/shared/ui/badge';
import { Modal } from '@/src/shared/ui/modal';
import { Spinner } from '@/src/shared/ui/spinner';
import { formatDateTime } from '@/src/shared/lib/formatters';
import { GATEWAY_STATUS_LABELS, UI_LABELS } from '@/src/shared/config/labels';
import { useRealtimeRefresh } from '@/src/shared/api/realtime';

const formatValue = (item: unknown) =>
  item === null || item === undefined || item === '' ? 'Chưa có' : String(item);

const formatAge = (seconds: number | null) =>
  seconds === null ? 'Chưa có' : `${Math.round(seconds)} giây`;

const bufferLabel = (status: GatewayOperationalState['buffer_status']) => {
  if (status === 'ONLINE') return 'Bình thường';
  if (status === 'DEGRADED') return 'Cảnh báo';
  if (status === 'OFFLINE') return 'Ngoại tuyến';
  return status || 'Chưa có';
};

const gatewayStatusLabel = (status: string | undefined): string => {
  if (!status) return 'Chưa rõ';
  const upper = status.toUpperCase();
  if (upper in GATEWAY_STATUS_LABELS) {
    return GATEWAY_STATUS_LABELS[upper as keyof typeof GATEWAY_STATUS_LABELS];
  }
  return status;
};

const statusBadgeVariant = (
  status: string | undefined
): 'success' | 'warning' | 'error' | 'neutral' => {
  switch (status?.toUpperCase()) {
    case 'ONLINE':
    case 'HEALTHY':
      return 'success';
    case 'DEGRADED':
    case 'WARNING':
      return 'warning';
    case 'OFFLINE':
    case 'ERROR':
      return 'error';
    default:
      return 'neutral';
  }
};

export const GatewaysPage: React.FC = () => {
  const [gateways, setGateways] = useState<GatewayOperationalState[]>([]);
  const [selected, setSelected] = useState<GatewayOperationalState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setGateways(await listGateways());
    } catch (reason: any) {
      setError(reason.message || 'Không thể kết nối đến máy chủ Backend.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') {
        void refresh();
      }
    }, 10_000);
    return () => clearInterval(timer);
  }, [refresh]);

  useRealtimeRefresh(() => {
    void refresh();
  }, ['api']);

  return (
    <div className="flex flex-col min-h-full w-full overflow-x-hidden">
      {/* 1. Standardized Global Page Header */}
      <header className="bg-surface border-b border-border shadow-2xs shrink-0">
        <div className="w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-3.5 sm:py-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4">
            <div>
              <h1 className="text-lg sm:text-xl font-bold font-sans tracking-tight text-text flex items-center gap-2.5">
                <Server className="w-5 h-5 text-primary" />
                <span>{UI_LABELS.nav.gateways}</span>
              </h1>
              <p className="text-xs text-text-muted mt-0.5 font-sans">
                Giám sát trạng thái hoạt động và lưu lượng đồng bộ của các Gateway quản trị phòng máy.
              </p>
            </div>

            <div className="flex items-center gap-2.5 shrink-0">
              <Button
                variant="outline"
                size="sm"
                onClick={() => void refresh()}
                isLoading={loading}
                leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
              >
                Làm mới
              </Button>
            </div>
          </div>
        </div>
      </header>

      {/* 2. Main Content Area */}
      <div className="w-full max-w-[1600px] mx-auto p-4 sm:p-6 lg:p-8 space-y-6 flex-1 min-w-0">
        {error && (
          <div className="p-4 bg-error-soft border border-error/30 rounded-sm text-sm text-error flex items-center gap-3">
            <AlertCircle className="w-5 h-5 shrink-0" />
            <span className="font-medium">{error}</span>
          </div>
        )}

        {loading ? (
          <div className="bg-surface border border-border rounded-sm p-12 flex justify-center shadow-2xs">
            <Spinner label="Đang tải danh sách Gateway..." />
          </div>
        ) : gateways.length === 0 ? (
          <div className="bg-surface border border-border rounded-sm p-8 sm:p-12 text-center space-y-3 shadow-2xs">
            <div className="w-12 h-12 rounded-full bg-surface-subtle border border-border mx-auto flex items-center justify-center text-text-subtle">
              <Router className="w-6 h-6" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-text font-sans">Không tìm thấy Gateway nào</h3>
              <p className="text-xs text-text-muted mt-1 font-sans">
                Chưa có Gateway cục bộ nào được đăng ký hoặc kết nối tới máy chủ trung tâm.
              </p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {gateways.map((gateway) => (
              <Card
                key={gateway.gateway_id}
                variant="interactive"
                onClick={() => setSelected(gateway)}
                className="p-5 hover:border-primary/50 transition-all cursor-pointer"
              >
                <div className="flex items-start justify-between gap-3 border-b border-border pb-3">
                  <div>
                    <h2 className="font-bold text-base font-sans text-text flex items-center gap-2">
                      <Network className="w-4 h-4 text-primary" />
                      {gateway.gateway_id}
                    </h2>
                    <p className="text-xs text-text-muted mt-0.5 font-sans">
                      Phòng {gateway.room_id} &bull; Phiên bản v{gateway.version}
                    </p>
                  </div>
                  <Badge variant={statusBadgeVariant(gateway.status)} dot size="sm">
                    {gatewayStatusLabel(gateway.status)}
                  </Badge>
                </div>

                <dl className="grid grid-cols-2 gap-3 mt-4 text-xs font-sans">
                  <div>
                    <dt className="text-text-muted">Kết nối Backend (Uplink)</dt>
                    <dd className="font-semibold text-text mt-0.5">
                      {gatewayStatusLabel(gateway.backend_uplink_status)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-text-muted">Máy trạm kết nối</dt>
                    <dd className="font-semibold text-text mt-0.5">
                      {formatValue(gateway.connected_agent_count)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-text-muted">Sự kiện chờ gửi</dt>
                    <dd className="font-semibold text-text mt-0.5">
                      {formatValue(gateway.pending_event_count)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-text-muted">Bộ đệm sự kiện</dt>
                    <dd className="font-semibold text-text mt-0.5">
                      {bufferLabel(gateway.buffer_status)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-text-muted">Sự kiện chờ lâu nhất</dt>
                    <dd className="font-semibold text-text mt-0.5">
                      {formatAge(gateway.oldest_pending_event_age)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-text-muted">Lần cuối phản hồi</dt>
                    <dd className="font-semibold text-text mt-0.5">
                      {gateway.last_seen ? formatDateTime(gateway.last_seen) : 'Chưa có'}
                    </dd>
                  </div>
                </dl>
              </Card>
            ))}
          </div>
        )}

        {/* Modal chi tiết Gateway */}
        <Modal
          isOpen={Boolean(selected)}
          onClose={() => setSelected(null)}
          title={`Chi tiết Gateway: ${selected?.gateway_id || ''}`}
          description={`Phòng máy ${selected?.room_id || ''} - Phiên bản v${selected?.version || ''}`}
          maxWidth="lg"
        >
          {selected && (
            <div className="space-y-4">
              <div className="flex items-center justify-between p-3 bg-surface-subtle border border-border rounded-sm">
                <div className="flex items-center gap-2">
                  <Network className="w-5 h-5 text-primary" />
                  <span className="font-bold text-sm text-text font-mono">
                    {selected.gateway_id}
                  </span>
                </div>
                <Badge variant={statusBadgeVariant(selected.status)} dot size="sm">
                  {gatewayStatusLabel(selected.status)}
                </Badge>
              </div>

              <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3.5 text-xs font-sans">
                <div className="p-3 bg-surface border border-border rounded-sm">
                  <dt className="text-text-muted">Phòng máy</dt>
                  <dd className="font-bold text-text text-sm mt-0.5">{selected.room_id}</dd>
                </div>
                <div className="p-3 bg-surface border border-border rounded-sm">
                  <dt className="text-text-muted">Phiên bản Gateway</dt>
                  <dd className="font-bold text-text text-sm mt-0.5">{selected.version}</dd>
                </div>
                <div className="p-3 bg-surface border border-border rounded-sm">
                  <dt className="text-text-muted">Kết nối Backend (Uplink)</dt>
                  <dd className="font-bold text-text text-sm mt-0.5">
                    {gatewayStatusLabel(selected.backend_uplink_status)}
                  </dd>
                </div>
                <div className="p-3 bg-surface border border-border rounded-sm">
                  <dt className="text-text-muted">Số lượng máy trạm kết nối</dt>
                  <dd className="font-bold text-text text-sm mt-0.5">
                    {formatValue(selected.connected_agent_count)}
                  </dd>
                </div>
                <div className="p-3 bg-surface border border-border rounded-sm">
                  <dt className="text-text-muted">Số sự kiện đang chờ đẩy</dt>
                  <dd className="font-bold text-text text-sm mt-0.5">
                    {formatValue(selected.pending_event_count)}
                  </dd>
                </div>
                <div className="p-3 bg-surface border border-border rounded-sm">
                  <dt className="text-text-muted">Trạng thái bộ đệm RAM</dt>
                  <dd className="font-bold text-text text-sm mt-0.5">
                    {bufferLabel(selected.buffer_status)}
                  </dd>
                </div>
                <div className="p-3 bg-surface border border-border rounded-sm">
                  <dt className="text-text-muted">Tuổi sự kiện chờ lâu nhất</dt>
                  <dd className="font-bold text-text text-sm mt-0.5">
                    {formatAge(selected.oldest_pending_event_age)}
                  </dd>
                </div>
                <div className="p-3 bg-surface border border-border rounded-sm">
                  <dt className="text-text-muted">Lần đẩy dữ liệu thành công cuối</dt>
                  <dd className="font-bold text-text text-sm mt-0.5">
                    {selected.last_flush_success_at
                      ? formatDateTime(selected.last_flush_success_at)
                      : 'Chưa có'}
                  </dd>
                </div>
                <div className="p-3 bg-surface border border-border rounded-sm sm:col-span-2">
                  <dt className="text-text-muted">Lỗi lần đẩy dữ liệu gần nhất</dt>
                  <dd className="font-mono text-xs text-error mt-1 break-words">
                    {formatValue(selected.last_flush_error)}
                  </dd>
                </div>
              </dl>

              <div className="flex justify-end pt-2">
                <Button variant="secondary" size="sm" onClick={() => setSelected(null)}>
                  Đóng
                </Button>
              </div>
            </div>
          )}
        </Modal>
      </div>
    </div>
  );
};
