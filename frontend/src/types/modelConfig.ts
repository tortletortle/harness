export interface ModelChoice {
  provider: string;
  model: string;
  temperature?: number;
  max_tokens?: number;
}
export interface ModelRole {
  primary: ModelChoice;
  fallback?: ModelChoice;
}
export interface ModelConfiguration {
  schema_version?: number;
  probe_version?: number;
  revision?: string;
  base_urls: Record<string, string>;
  models?: Record<string, ModelRole>;
  agents?: Record<string, ModelRole>;
  api_keys_configured: Record<string, boolean>;
  environment_overrides?: string[];
  requires_restart?: boolean;
}
export interface ModelConfigUpdate {
  revision: string;
  base_urls: Record<string, string>;
  models: Record<
    string,
    {
      primary: Pick<ModelChoice, 'provider' | 'model'>;
      fallback?: Pick<ModelChoice, 'provider' | 'model'>;
    }
  >;
}

export interface ModelProbeRequest {
  provider: string;
  revision: string;
  mode: 'connectivity' | 'model';
  model?: string;
  acknowledge_cost?: boolean;
}
export interface ModelProbeResult {
  ok: boolean;
  code: string;
  message: string;
  provider: string;
  mode: 'connectivity' | 'model';
  model: string | null;
  reachable: boolean;
  http_status: number | null;
  latency_ms: number;
  models: string[];
}
