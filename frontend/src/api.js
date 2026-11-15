export const productImagePattern = '/product-images/[a-z0-9][a-z0-9_\\-]*\\.webp'

export const merchant = {
  id: import.meta.env.VITE_MERCHANT_ID || '3',
  name: import.meta.env.VITE_MERCHANT_NAME || 'Northstar Retail',
}

function csrfToken() {
  const cookie = document.cookie.split('; ').find((entry) => entry.startsWith('csrftoken='))
  return cookie ? decodeURIComponent(cookie.slice('csrftoken='.length)) : ''
}

export function signIn() {
  const next = window.location.pathname + window.location.search
  window.location.assign('/login/?' + new URLSearchParams({ next }))
}

export async function signOut() {
  const response = await fetch('/logout/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'X-CSRFToken': csrfToken() },
  })
  if (!response.ok) throw new Error('Could not sign out. Refresh the page and try again.')
}

function errorMessage(data, fallback) {
  if (typeof data?.detail === 'string') return data.detail
  if (!data || typeof data !== 'object') return fallback
  return Object.entries(data).map(([field, messages]) => {
    const message = Array.isArray(messages) ? messages.join(' ') : String(messages)
    return field === 'non_field_errors' ? message : field.replaceAll('_', ' ') + ' — ' + message
  }).join(' ') || fallback
}

async function request(path, { params, signal, body, method } = {}) {
  const query = new URLSearchParams({ merchant_id: merchant.id, ...params })
  const headers = { Accept: 'application/json' }
  if (body) {
    headers['Content-Type'] = 'application/json'
    headers['X-CSRFToken'] = csrfToken()
  }
  const response = await fetch('/api/products/' + path + '?' + query, {
    method: method || (body ? 'PATCH' : 'GET'),
    credentials: 'same-origin',
    signal,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    if (response.status === 401 || (
      response.status === 403 && data?.detail === 'Authentication credentials were not provided.'
    )) {
      signIn()
      throw new Error('Sign in to continue.')
    }
    throw new Error(errorMessage(data, 'Request failed (' + response.status + '). Please try again.'))
  }
  if (data === null) throw new Error('The server returned an invalid response. Please try again.')
  return data
}

export const products = {
  list(search, page, signal) {
    return request('', { params: { search, page }, signal })
  },
  editor(id, signal) {
    return request(id + '/editor/', { signal })
  },
  detail(id, signal) {
    return request(id + '/', { signal })
  },
  update(id, body) {
    return request(id + '/', { body })
  },
  activity(id, page, signal) {
    return request(id + '/activity/', { params: { page }, signal })
  },
  adjustInventory(id, variantId, body) {
    return request(id + '/variants/' + variantId + '/adjustments/', { body, method: 'POST' })
  },
}

// Mirrors ADJUSTMENT_REASONS in backend/catalog/models.py. The server validates the choice.
export const adjustmentReasons = {
  increase: [
    { value: 'physical_count', label: 'Physical count' },
    { value: 'returned_to_stock', label: 'Returned to stock' },
    { value: 'found_stock', label: 'Found stock' },
    { value: 'other', label: 'Other' },
  ],
  decrease: [
    { value: 'physical_count', label: 'Physical count' },
    { value: 'damaged', label: 'Damaged' },
    { value: 'lost_or_stolen', label: 'Lost or stolen' },
    { value: 'expired', label: 'Expired' },
    { value: 'other', label: 'Other' },
  ],
}
