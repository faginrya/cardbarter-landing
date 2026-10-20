import test from 'node:test';
import assert from 'node:assert/strict';
import { serializeCaptureEntries, submitCapture } from './capture-form.mjs';

test('waitlist serialization normalizes email and groups metadata', () => {
  assert.deepEqual(serializeCaptureEntries([
    ['email', ' Test@Example.COM '], ['source', 'homepage'],
    ['consent', 'on'], ['meta[utm_source]', 'newsletter'], ['website_url', ''],
  ]), {
    email: 'test@example.com', source: 'homepage', consent: true,
    meta: { utm_source: 'newsletter' }, website_url: '',
  });
});

test('founding-member serialization converts all consent fields to booleans', () => {
  assert.deepEqual(serializeCaptureEntries([
    ['email', 'founder@example.com'], ['agreeUploadCards', 'true'],
    ['agreeUseApp', 'on'], ['agreeProvideFeedback', 'true'],
  ]), {
    email: 'founder@example.com', agreeUploadCards: true,
    agreeUseApp: true, agreeProvideFeedback: true, website_url: '',
  });
});

test('submitCapture POSTs JSON to the injected API origin and preserves the payload', async () => {
  let request;
  const payload = { email: 'test@example.com', website_url: '' };
  const response = { status: 201 };
  const result = await submitCapture({
    action: '/capture/waitlist', payload, apiBase: 'https://verified-api.example',
    fetchImpl: async (url, options) => { request = { url, options }; return response; },
  });
  assert.equal(request.url, 'https://verified-api.example/capture/waitlist');
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.headers['Content-Type'], 'application/json');
  assert.equal(request.options.headers.Accept, 'application/json');
  assert.deepEqual(JSON.parse(request.options.body), payload);
  assert.deepEqual(result, { skipped: false, response });
});

test('founding-member endpoint uses the injected API origin', async () => {
  let requestUrl;
  await submitCapture({
    action: '/capture/founding-members/apply', payload: { website_url: '' },
    apiBase: 'https://verified-api.example',
    fetchImpl: async (url) => { requestUrl = url; return { status: 201 }; },
  });
  assert.equal(requestUrl, 'https://verified-api.example/capture/founding-members/apply');
});

test('filled honeypot returns before fetch', async () => {
  let called = false;
  const result = await submitCapture({
    action: '/capture/waitlist', payload: { website_url: 'https://spam.example' },
    apiBase: 'https://verified-api.example',
    fetchImpl: async () => { called = true; },
  });
  assert.equal(called, false);
  assert.deepEqual(result, { skipped: true });
});
