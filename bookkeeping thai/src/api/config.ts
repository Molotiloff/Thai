export interface ApiRuntimeConfig {
  baseUrl: string;
  wsUrl: string;
  openApiUrl: string;
  useMocks: boolean;
}

function readBoolean(value: string | undefined): boolean | null {
  if (value === undefined) return null;
  if (value === '1' || value.toLowerCase() === 'true') return true;
  if (value === '0' || value.toLowerCase() === 'false') return false;
  return null;
}

export function getApiRuntimeConfig(): ApiRuntimeConfig {
  const baseUrl =
    process.env.CRM_API_BASE_URL ??
    process.env.NEXT_PUBLIC_CRM_API_BASE_URL ??
    '';
  const backendBaseUrl = baseUrl.replace(/\/api\/v1\/?$/, '');
  const frontendOrigin = (process.env.CRM_FRONTEND_ORIGIN ?? '').replace(/\/+$/, '');
  const explicitUseMocks = readBoolean(process.env.NEXT_PUBLIC_USE_MOCKS);
  const explicitUseApi = readBoolean(process.env.CRM_USE_API);

  return {
    baseUrl,
    wsUrl:
      process.env.CRM_WS_URL ??
      process.env.NEXT_PUBLIC_CRM_WS_URL ??
      (frontendOrigin || backendBaseUrl).replace(/^http/, 'ws') + '/ws',
    openApiUrl:
      process.env.CRM_OPENAPI_URL ??
      (backendBaseUrl ? `${backendBaseUrl}/openapi.json` : ''),
    useMocks: explicitUseMocks ?? !explicitUseApi,
  };
}
