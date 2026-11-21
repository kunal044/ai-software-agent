import { useEffect, useState } from 'react'
import axios from 'axios'

export const base = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
export const api = axios.create({ baseURL: base + '/api', timeout: 600000 })

function errorText(error) {
  const detail = error.response?.data?.detail
  if (Array.isArray(detail)) return detail.map(item => item.msg).join(', ')
  return (typeof detail === 'object' ? detail?.message : detail) || (error.response ? 'The request failed.' : 'Could not complete the request. Check that FastAPI is running at ' + base + '.')
}

export function useData(path, revision = 0) {
  const [result, setResult] = useState({})
  const key = path + ':' + revision
  useEffect(() => {
    if (!path) return
    const controller = new AbortController()
    api.get(path, { signal: controller.signal }).then(
      ({ data }) => setResult({ key, data }),
      error => { if (!axios.isCancel(error)) setResult({ key, error: errorText(error), status: error.response?.status, errorCode: error.response?.data?.detail?.code }) },
    )
    return () => controller.abort()
  }, [key, path])
  return result.key === key ? result : { loading: Boolean(path) }
}

export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function run(action) {
    setBusy(true)
    setError('')
    try {
      await action()
    } catch (failure) {
      setError(errorText(failure))
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, run }
}

export function workspacePath(repoId, view = '') {
  return '/repositories/' + repoId + '/workspace' + (view ? '/' + view : '')
}

export function sourceQuery(path, start, end) {
  const query = new URLSearchParams({ path })
  if (start) query.set('start', start)
  if (end) query.set('end', end)
  return '?' + query.toString()
}
