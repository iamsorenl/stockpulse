import { useMemo } from 'react'
import { ChatPanel, fetchTransport, useChat } from 'chat-ui'
import 'chat-ui/styles.css'
import { apiUrl } from '../api'

// Q&A about one ticker, answered from StockPulse's own price + sentiment data.
// Mount with key={ticker} so each ticker gets a fresh chat (history persists
// per ticker in sessionStorage via storageKey).
export function TickerChat({ ticker }: { ticker: string }) {
  const send = useMemo(
    () =>
      fetchTransport(apiUrl('/api/chat'), {
        body: (m) => ({ ticker, messages: m }),
      }),
    [ticker],
  )
  const chat = useChat({ send, storageKey: 'stockpulse-chat-' + ticker })

  return (
    <section className="sentiment ticker-chat" aria-label={`Ask about ${ticker}`}>
      <div className="sentiment-head">
        <h2 className="sentiment-title">Ask about {ticker}</h2>
      </div>
      <ChatPanel
        messages={chat.messages}
        onSend={chat.send}
        onStop={chat.stop}
        status={chat.status}
        error={chat.error}
        label={`Question about ${ticker}`}
        placeholder={`e.g. How has ${ticker} trended lately?`}
      />
    </section>
  )
}
