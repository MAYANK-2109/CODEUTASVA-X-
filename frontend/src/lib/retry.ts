// The backend runs on a small host that restarts when it is redeployed or has
// been idle. While it is coming back the browser sees a network error or a
// 502/503/504 from the host's proxy, so a request is tried again instead of failing.
const WAITS_MS = [4000, 8000, 12000, 16000, 20000]
const RESTARTING = new Set([502, 503, 504])

export const RETRY_ATTEMPTS = WAITS_MS.length + 1

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

// onRetry is told which attempt is about to start (2, 3, ...).
export async function fetchWithRetry(
  url: string,
  init?: RequestInit,
  onRetry?: (attempt: number) => void,
): Promise<Response> {
  for (let attempt = 1; ; attempt++) {
    const last = attempt === RETRY_ATTEMPTS
    try {
      const response = await fetch(url, init)
      if (!RESTARTING.has(response.status) || last) return response
    } catch (e) {
      // A TypeError is the browser's "could not reach the server"; anything else (an abort) is final.
      if (!(e instanceof TypeError) || last) throw e
    }
    onRetry?.(attempt + 1)
    await sleep(WAITS_MS[attempt - 1])
  }
}

// Start the backend waking as soon as the app opens, so it is up by the time a page needs it.
export function wakeBackend(baseUrl: string): void {
  fetch(`${baseUrl}/health`).catch(() => {})
}
