import { useEffect, useState } from 'react'

// Returns a copy of `value` that only updates after it has stopped changing for
// `delayMs`. Used to keep the search box from firing a request per keystroke.
export function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState<T>(value)

  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(id)
  }, [value, delayMs])

  return debounced
}
