// capture-form.mjs — reference implementation (LOCKED pure boundary)
const consentFields = new Set(['consent', 'agreeUploadCards', 'agreeUseApp', 'agreeProvideFeedback']);
const metaFields = new Set(['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'section']);

export function serializeCaptureEntries(entries) {
  const payload = { website_url: '' };
  for (const [key, value] of entries) {
    const metaKey = /^meta\[([^\]]+)\]$/.exec(key)?.[1];
    if (metaKey) {
      if (metaFields.has(metaKey)) {
        payload.meta ??= {};
        payload.meta[metaKey] = value.slice(0, 200);
      }
    } else if (consentFields.has(key)) {
      payload[key] = value === 'true' || value === 'on';
    } else {
      payload[key] = key === 'email' ? value.trim().toLowerCase() : value;
    }
  }
  return payload;
}

export async function submitCapture({ action, payload, fetchImpl, apiBase }) {
  if (typeof payload.website_url === 'string' && payload.website_url.length > 0) {
    return { skipped: true };
  }
  const response = await fetchImpl(new URL(action, apiBase).toString(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(payload),
  });
  return { skipped: false, response };
}

// ---------------------------------------------------------------------------------------------
// Browser adapter. Runs only when a real document exists.
// ---------------------------------------------------------------------------------------------
const MESSAGES = {
  waitlist: {
    201: "You're on the list. Check your inbox for a confirmation.",
    200: "You're already on the list.",
    202: "Thanks — you're on the list.",
    429: 'Too many attempts. Please wait a few minutes and try again.',
    503: 'Early access signup is not open yet. Please check back soon.',
  },
  founding: {
    201: 'Application received. Check your inbox for a confirmation.',
    200: 'We already have an application from this email.',
    202: 'Thanks — your application was received.',
    429: 'Too many attempts. Please wait a few minutes and try again.',
    503: 'Founding Member applications are not open yet. Please check back soon.',
  },
};

async function onSubmit(event) {
  const form = event.currentTarget;
  event.preventDefault();
  const kind = form.dataset.captureForm === 'founding' ? 'founding' : 'waitlist';
  const status = form.querySelector('[data-capture-status]');
  const submit = form.querySelector('button[type="submit"]');
  const payload = serializeCaptureEntries(new FormData(form).entries());
  const apiBase = window.__CAPTURE_API_BASE__;

  if (submit) submit.disabled = true;
  if (status) status.textContent = 'Sending…';

  try {
    const { skipped, response } = await submitCapture({
      action: kind === 'founding' ? '/capture/founding-members/apply' : '/capture/waitlist',
      payload,
      fetchImpl: (...args) => fetch(...args),
      apiBase,
    });
    if (status) {
      // Static copy only: never render server response content into the page.
      status.textContent = skipped ? MESSAGES[kind][202] : (MESSAGES[kind][response.status] ?? 'Something went wrong. Please try again.');
    }
    if (!skipped && response.ok) form.reset();
  } catch {
    if (status) status.textContent = 'Network error. Please try again.';
  } finally {
    if (submit) submit.disabled = false;
  }
}

export function initCaptureForms(root = document) {
  root.querySelectorAll('[data-capture-form]').forEach((form) => {
    form.addEventListener('submit', onSubmit);
  });
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => initCaptureForms());
  } else {
    initCaptureForms();
  }
}
