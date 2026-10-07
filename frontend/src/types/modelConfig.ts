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
  /** Present when GET /api/config/catalog is available. */
  catalog_version?: number;
  revision?: string;
  base_urls: Record<string, string>;
  models?: Record<string, ModelRole>;
  agents?: Record<string, ModelRole>;
  api_keys_configured: Record<string, boolean>;
  /** Key comes from the encrypted local store rather than an environment variable. */
  api_keys_persisted?: Record<string, boolean>;
  environment_overrides?: string[];
  /** Providers whose API key currently comes from the process environment. */
  api_key_environment_overrides?: string[];
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

/** Server-side cache of one provider's fetched model IDs (never secrets). */
export interface ModelCatalogEntry {
  models: string[];
  fetched_at: string | null;
  base_url: string | null;
  /** Fetched against a different endpoint than the one now in effect. */
  stale: boolean;
}
export type ModelCatalog = Record<string, ModelCatalogEntry>;
