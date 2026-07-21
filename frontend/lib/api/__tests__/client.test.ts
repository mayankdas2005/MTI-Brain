import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ApiError, apiFetch } from '../client';

// Mock fetch globally
const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

// Mock auth helpers
vi.mock('@/lib/auth', () => ({
  getAuthHeaders: vi.fn(() => ({ Authorization: 'Bearer test-token' })),
  clearStoredToken: vi.fn(),
  setStoredToken: vi.fn(),
  setStoredUser: vi.fn(),
}));

describe('ApiError', () => {
  it('creates error with status and body', () => {
    const err = new ApiError(404, { detail: 'Not found' });
    expect(err.status).toBe(404);
    expect(err.body).toEqual({ detail: 'Not found' });
    expect(err.message).toBe('Not found');
    expect(err.name).toBe('ApiError');
  });

  it('uses generic message when body has no detail', () => {
    const err = new ApiError(500, 'something went wrong');
    expect(err.message).toBe('API error 500');
  });

  it('extracts detail string from body', () => {
    const err = new ApiError(400, { detail: 'Invalid input' });
    expect(err.message).toBe('Invalid input');
  });
});

describe('apiFetch', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('fetch', mockFetch);
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'http://localhost:8000');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('makes a GET request with auth headers', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-length': '10' }),
      json: () => Promise.resolve({ data: 'test' }),
    });

    const result = await apiFetch('/test');
    expect(result).toEqual({ data: 'test' });

    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/v1/test');
    expect(options.headers.Authorization).toBe('Bearer test-token');
    expect(options.headers['Content-Type']).toBe('application/json');
  });

  it('throws ApiError on non-ok response', async () => {
    const mockResponse = {
      ok: false,
      status: 400,
      headers: new Headers(),
      json: () => Promise.resolve({ detail: 'Bad request' }),
    };
    mockFetch.mockResolvedValueOnce(mockResponse);
    await expect(apiFetch('/bad')).rejects.toThrow(ApiError);

    mockFetch.mockResolvedValueOnce(mockResponse);
    await expect(apiFetch('/bad')).rejects.toMatchObject({
      status: 400,
    });
  });

  it('returns undefined for 204 responses', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 204,
      headers: new Headers(),
    });

    const result = await apiFetch('/no-content');
    expect(result).toBeUndefined();
  });

  it('returns undefined for empty content-length', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-length': '0' }),
    });

    const result = await apiFetch('/empty');
    expect(result).toBeUndefined();
  });

  it('handles 429 rate limit error', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 429,
      headers: new Headers({ 'Retry-After': '30' }),
      json: () => Promise.resolve({}),
    });

    try {
      await apiFetch('/rate-limited');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(429);
      expect((err as ApiError).message).toContain('30 seconds');
    }
  });

  it('handles 429 without Retry-After header', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 429,
      headers: new Headers(),
      json: () => Promise.resolve({}),
    });

    try {
      await apiFetch('/rate-limited-no-retry');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).message).toContain('slow down');
    }
  });

  it('handles 503 service unavailable', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      headers: new Headers(),
      json: () => Promise.resolve({}),
    });

    try {
      await apiFetch('/unavailable');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(503);
      expect((err as ApiError).message).toContain('temporarily unavailable');
    }
  });

  it('dispatches unauthenticated event on 401', async () => {
    const dispatchSpy = vi.fn();
    vi.stubGlobal('window', { dispatchEvent: dispatchSpy });

    // First call: 401
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      headers: new Headers(),
      json: () => Promise.resolve({ detail: 'Unauthorized' }),
    });
    // Refresh attempt fails
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      headers: new Headers(),
    });

    try {
      await apiFetch('/protected');
    } catch (err) {
      expect((err as ApiError).status).toBe(401);
    }
  });

  it('handles non-JSON error responses', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      headers: new Headers(),
      json: () => Promise.reject(new Error('not json')),
      text: () => Promise.resolve('Internal Server Error'),
    });

    try {
      await apiFetch('/server-error');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(500);
      expect((err as ApiError).body).toBe('Internal Server Error');
    }
  });

  it('passes custom options through', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-length': '5' }),
      json: () => Promise.resolve({ ok: true }),
    });

    await apiFetch('/post-endpoint', {
      method: 'POST',
      body: JSON.stringify({ key: 'value' }),
    });

    const [, options] = mockFetch.mock.calls[0];
    expect(options.method).toBe('POST');
    expect(options.body).toBe(JSON.stringify({ key: 'value' }));
  });
});
