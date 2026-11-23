/**
 * ReviewsV2.jsx — Sell › Reviews (23 Nov 2026). What customers said about their orders: the
 * average and how many of each star (counted on the server from every rating), the latest
 * ratings with their order number, and the Google review link Vula sends to happy customers.
 * Ratings come in on WhatsApp after an order is delivered; 3 stars or fewer also alerts the team.
 */
import { MessageSquareHeart, Star } from 'lucide-react'
import { Button, Card, EmptyState, ErrorCard, Field, Input, PageHeader, Skeleton, StatusChip } from '../ui/index.jsx'
import { useReviews } from './useReviews'
import './reviews.css'

const when = (iso) => {
  const d = iso ? new Date(iso) : null
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', year: 'numeric' }) : ''
}

function Stars({ n, label }) {
  return (
    <span className="vrv-stars" role="img" aria-label={label || `${n} out of 5 stars`}>
      {[1, 2, 3, 4, 5].map((i) => <Star key={i} size={16} aria-hidden="true" className={i <= n ? 'vrv-star--on' : 'vrv-star--off'} />)}
    </span>
  )
}

export default function ReviewsV2({ tenantId }) {
  const r = useReviews(tenantId)
  const d = r.data
  const total = d?.count || 0
  return (
    <div className="vrv">
      <PageHeader sub="Ratings customers give on WhatsApp after their order is delivered. A rating of 3 or less alerts your team so someone can follow up." />

      {r.loading ? <Card><Skeleton lines={4} /></Card> : r.failed ? <ErrorCard what="your reviews" onRetry={r.load} /> : (
        <>
          <Card className="vrv-summary">
            <div className="vrv-avg">
              <span className="vrv-avg-n">{d.average != null ? d.average.toFixed(1) : '–'}</span>
              <div>
                {d.average != null && <Stars n={Math.round(d.average)} label={`Average ${d.average.toFixed(1)} out of 5`} />}
                <p className="vrv-sub">{total === 1 ? '1 rating' : `${total} ratings`}{d.low ? ` · ${d.low} of 3 stars or less` : ''}</p>
              </div>
            </div>
            <ul className="vrv-bars" aria-label="Ratings by stars">
              {['5', '4', '3', '2', '1'].map((s) => {
                const n = d.stars?.[s] || 0
                const pct = total ? Math.round((n / total) * 100) : 0
                return (
                  <li key={s} className="vrv-bar">
                    <span className="vrv-bar-l">{s} ★</span>
                    <span className="vrv-bar-track" aria-hidden="true"><span className="vrv-bar-fill" style={{ width: `${pct}%` }} /></span>
                    <span className="vrv-bar-n">{n}</span>
                  </li>
                )
              })}
            </ul>
          </Card>

          <Card>
            <form className="vrv-link" onSubmit={r.saveLink}>
              <h2 className="vrv-h">Google review link</h2>
              <p className="vrv-sub">
                When a customer gives 4 or 5 stars, Vula's thank-you also asks them for a Google review with this link.
                Get it in your Google Business Profile: choose <strong>Ask for reviews</strong> and copy the link.
              </p>
              <Field label="Link">
                <Input type="url" inputMode="url" placeholder="https://g.page/r/…" value={r.link}
                  onChange={(e) => r.setLink(e.target.value)} />
              </Field>
              <div className="vrv-row">
                <Button type="submit" disabled={r.saving}>{r.saving ? 'Saving…' : 'Save link'}</Button>
                {r.linkMsg && <p className={r.linkMsg.tone === 'ok' ? 'vrv-ok' : 'vrv-error'} role="status">{r.linkMsg.text}</p>}
              </div>
            </form>
          </Card>

          {total === 0 ? (
            <EmptyState Icon={MessageSquareHeart} title="No ratings yet">
              When an order is marked delivered, Vula asks the customer how it was. Their ratings show here.
            </EmptyState>
          ) : (
            <ul className="vrv-list" aria-label="Latest ratings">
              {d.reviews.map((x) => (
                <li key={x.id}>
                  <Card className="vrv-item">
                    <div className="vrv-row">
                      <Stars n={x.rating} />
                      {x.rating <= 3 && <StatusChip tone="warn">Follow up</StatusChip>}
                      <span className="vrv-sub vrv-date">{when(x.created_at)}</span>
                    </div>
                    <p className="vrv-who">
                      {x.customer_name || x.customer_phone || 'A customer'}
                      {x.order_number ? <span className="vrv-sub"> · order {x.order_number}</span> : null}
                    </p>
                    {x.comment && <p className="vrv-comment">{x.comment}</p>}
                  </Card>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
