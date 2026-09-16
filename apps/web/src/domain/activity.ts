export interface ActivityItem {
  id: string;
  timestamp: string;
  level: 'INFO' | 'WARNING' | 'ERROR' | 'SUCCESS';
  message: string;
  source: string;
  details?: {
    code?: string;
    payload?: Record<string, any> | string;
    remediation?: string;
    rawJson?: string;
  };
}
