export class ApiError extends Error {
  readonly status: number;
  readonly data: unknown;

  constructor(status: number, message: string, data: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

export interface ApiClientOptions {
  baseUrl: string;
  getHeaders?: () => Promise<HeadersInit | null> | HeadersInit | null;
  getToken?: () => Promise<string | null> | string | null;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly getHeaders?: ApiClientOptions['getHeaders'];
  private readonly getToken?: ApiClientOptions['getToken'];

  constructor({ baseUrl, getHeaders, getToken }: ApiClientOptions) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.getHeaders = getHeaders;
    this.getToken = getToken;
  }

  get<T>(path: string, init?: RequestInit): Promise<T> {
    return this.request<T>(path, { ...init, method: 'GET' });
  }

  post<T>(path: string, body?: unknown, init?: RequestInit): Promise<T> {
    return this.request<T>(path, { ...init, method: 'POST', body: serializeBody(body) });
  }

  patch<T>(path: string, body?: unknown, init?: RequestInit): Promise<T> {
    return this.request<T>(path, { ...init, method: 'PATCH', body: serializeBody(body) });
  }

  delete<T>(path: string, init?: RequestInit): Promise<T> {
    return this.request<T>(path, { ...init, method: 'DELETE' });
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    if (!this.baseUrl) {
      throw new Error('CRM API base URL is not configured');
    }

    const headers = new Headers((await this.resolveHeaders()) ?? undefined);
    new Headers(init.headers).forEach((value, key) => headers.set(key, value));
    if (init.body !== undefined && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }

    const token = await this.resolveToken();
    if (token) {
      headers.set('Authorization', `Bearer ${token}`);
    }

    const response = await fetch(this.url(path), {
      cache: 'no-store',
      credentials: 'include',
      ...init,
      headers,
    });

    if (!response.ok) {
      const data = await readResponseBody(response);
      throw new ApiError(response.status, errorMessage(response.status, data), data);
    }

    if (response.status === 204) {
      return undefined as T;
    }

    return (await readResponseBody(response)) as T;
  }

  private url(path: string): string {
    if (/^https?:\/\//.test(path)) return path;
    return `${this.baseUrl}/${path.replace(/^\/+/, '')}`;
  }

  private async resolveToken(): Promise<string | null> {
    if (!this.getToken) return null;
    return this.getToken();
  }

  private async resolveHeaders(): Promise<HeadersInit | null> {
    if (!this.getHeaders) return null;
    return this.getHeaders();
  }
}

function serializeBody(body: unknown): BodyInit | undefined {
  if (body === undefined) return undefined;
  if (typeof body === 'string' || body instanceof FormData) return body;
  return JSON.stringify(body);
}

async function readResponseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) {
    return response.json();
  }
  return response.text();
}

function errorMessage(status: number, data: unknown): string {
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === 'string') return detail;
  }
  return `CRM API request failed with status ${status}`;
}
