import { useEffect, useMemo, useRef, useState } from 'react'
import { searchStocks, type SearchResult } from '../api'
import { useDebounced } from '../hooks/useDebounced'

interface SearchBoxProps {
  // Called when the user commits a ticker (click or Enter on a suggestion).
  onSelect: (symbol: string) => void
  // Currently-selected ticker, shown as a hint in the placeholder.
  selected: string | null
}

type ListState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'results'; items: SearchResult[] }
  | { kind: 'empty' }
  | { kind: 'error'; message: string }

const DEBOUNCE_MS = 250

export function SearchBox({ onSelect, selected }: SearchBoxProps) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [highlight, setHighlight] = useState(0)
  const [state, setState] = useState<ListState>({ kind: 'idle' })

  const debouncedQuery = useDebounced(query.trim(), DEBOUNCE_MS)
  const containerRef = useRef<HTMLDivElement>(null)

  // Fetch suggestions whenever the debounced query changes.
  useEffect(() => {
    if (debouncedQuery === '') {
      setState({ kind: 'idle' })
      return
    }
    const controller = new AbortController()
    setState({ kind: 'loading' })
    searchStocks(debouncedQuery, controller.signal)
      .then((items) => {
        setState(
          items.length === 0
            ? { kind: 'empty' }
            : { kind: 'results', items },
        )
        setHighlight(0)
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        const message = err instanceof Error ? err.message : String(err)
        setState({ kind: 'error', message })
      })
    return () => controller.abort()
  }, [debouncedQuery])

  // Close the dropdown on any click outside the component.
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  const items = useMemo(
    () => (state.kind === 'results' ? state.items : []),
    [state],
  )

  function commit(symbol: string) {
    onSelect(symbol)
    setQuery('')
    setState({ kind: 'idle' })
    setOpen(false)
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlight((h) => Math.min(h + 1, items.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlight((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      if (items[highlight]) {
        e.preventDefault()
        commit(items[highlight].symbol)
      }
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  const showDropdown = open && state.kind !== 'idle'

  return (
    <div className="searchbox" ref={containerRef}>
      <input
        className="searchbox-input"
        type="text"
        role="combobox"
        aria-expanded={showDropdown}
        aria-controls="searchbox-list"
        aria-autocomplete="list"
        autoComplete="off"
        spellCheck={false}
        placeholder={
          selected ? `${selected} — search another ticker…` : 'Search ticker or company (try "app")…'
        }
        value={query}
        onChange={(e) => {
          setQuery(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />

      {showDropdown && (
        <ul className="searchbox-list" id="searchbox-list" role="listbox">
          {state.kind === 'loading' && (
            <li className="searchbox-status">Searching…</li>
          )}
          {state.kind === 'empty' && (
            <li className="searchbox-status">
              No matches for “{debouncedQuery}”.
            </li>
          )}
          {state.kind === 'error' && (
            <li className="searchbox-status searchbox-status--error">
              {state.message}
            </li>
          )}
          {state.kind === 'results' &&
            items.map((item, i) => (
              <li
                key={item.symbol}
                role="option"
                aria-selected={i === highlight}
                className={
                  'searchbox-option' +
                  (i === highlight ? ' searchbox-option--active' : '')
                }
                onMouseEnter={() => setHighlight(i)}
                // onMouseDown (not onClick) so it fires before the input blur
                // that would otherwise close the dropdown first.
                onMouseDown={(e) => {
                  e.preventDefault()
                  commit(item.symbol)
                }}
              >
                <span className="searchbox-symbol">{item.symbol}</span>
                <span className="searchbox-name">{item.name}</span>
              </li>
            ))}
        </ul>
      )}
    </div>
  )
}
