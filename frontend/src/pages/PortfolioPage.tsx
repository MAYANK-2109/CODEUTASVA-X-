import React, { useState, useRef, useEffect, useCallback } from 'react'
import { useAuth } from '../context/AuthContext'
import { supabase } from '../lib/supabase'
import { isLeftover, savedPrice } from '../lib/holdings'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface Holding {
  id?: string
  user_id?: string
  name: string
  symbol: string
  isin: string
  type: 'STOCK' | 'MF' | 'ETF' | 'BOND' | 'COMMODITY'
  buy_date: string | null
  units: number | null
  buy_price: number | null
  current_price: number | null
  created_at?: string
}

type EditableHolding = Holding & { _rowKey: string }

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const BACKEND_URL: string =
  (import.meta as any).env?.VITE_BACKEND_URL ||
  (import.meta as any).env?.VITE_API_URL ||
  'https://codeutasva-x.onrender.com'

const PRICE_REFRESH_MS = 60_000

const TYPE_COLOURS: Record<string, string> = {
  STOCK:     'bg-blue-100 text-blue-700',
  MF:        'bg-purple-100 text-purple-700',
  ETF:       'bg-amber-100 text-amber-700',
  COMMODITY: 'bg-yellow-100 text-yellow-800 border border-yellow-200',
  BOND:      'bg-slate-100 text-slate-700',
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const fmt = (n: number | null | undefined, decimals = 2) =>
  n == null ? '—' : n.toLocaleString('en-IN', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })

const fmtCur = (n: number | null | undefined) =>
  n == null ? '—' : `\u20B9${fmt(n)}`

// Profit and loss need a real current price. Without one they are unknown,
// not zero.
function calcPnL(h: Holding) {
  if (h.units == null || h.buy_price == null || h.current_price == null) return null
  return (h.current_price - h.buy_price) * h.units
}

function pnlPct(h: Holding) {
  if (h.buy_price == null || h.buy_price === 0 || h.current_price == null) return null
  return ((h.current_price - h.buy_price) / h.buy_price) * 100
}

function avatarColor(s: string): string {
  const palette = ['#6366F1','#8B5CF6','#EC4899','#F59E0B','#10B981','#3B82F6','#14B8A6','#F97316','#EF4444','#06B6D4']
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) & 0xffffff
  return palette[Math.abs(h) % palette.length]
}

function StockLogo({ name, symbol }: { name: string; symbol: string }) {
  const [err, setErr] = useState(false)
  const ticker = (symbol || name || 'X').toUpperCase().split(' ')[0]
  const color   = avatarColor(ticker)
  if (!err) {
    return (
      <img
        src={`https://logo.clearbit.com/${ticker.toLowerCase()}.com?size=40`}
        alt={ticker}
        className="w-9 h-9 rounded-lg object-contain bg-white border border-gray-100 p-0.5"
        onError={() => setErr(true)}
      />
    )
  }
  return (
    <div
      className="w-9 h-9 rounded-lg flex items-center justify-center text-white text-xs font-bold shrink-0"
      style={{ background: color }}
    >
      {ticker.slice(0, 2)}
    </div>
  )
}

// ── Mini sparkline (96×48, matches reference image style) ──────────────────────

const MiniSparkline: React.FC<{ values: number[]; positive: boolean }> = ({ values, positive }) => {
  const W = 96, H = 48, padX = 4, padY = 6
  if (values.length < 2) {
    return (
      <svg width={W} height={H} aria-hidden style={{ display: 'block', margin: '0 auto' }}>
        <rect x={0} y={0} width={W} height={H} rx={6} fill={positive ? 'rgba(0,179,134,0.06)' : 'rgba(239,68,68,0.06)'} />
        <line x1={padX} y1={H / 2} x2={W - padX} y2={H / 2} stroke="#e5e7eb" strokeWidth="1.5" strokeDasharray="3 2" />
      </svg>
    )
  }
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const span = hi - lo || 1
  const px = (i: number) => padX + (i / (values.length - 1)) * (W - padX * 2)
  const py = (v: number) => H - padY - ((v - lo) / span) * (H - padY * 2)
  let d = ''
  // Smooth curve via cubic bezier
  values.forEach((v, i) => {
    if (i === 0) { d += `M${px(i).toFixed(1)},${py(v).toFixed(1)}` }
    else {
      const cpX = (px(i) + px(i - 1)) / 2
      d += ` C${cpX.toFixed(1)},${py(values[i - 1]).toFixed(1)} ${cpX.toFixed(1)},${py(v).toFixed(1)} ${px(i).toFixed(1)},${py(v).toFixed(1)}`
    }
  })
  const fillD = `${d} L${px(values.length - 1).toFixed(1)},${H} L${px(0).toFixed(1)},${H} Z`
  const color = positive ? '#00B386' : '#EF4444'
  const fillColor = positive ? 'rgba(0,179,134,0.13)' : 'rgba(239,68,68,0.11)'
  const dotX = px(values.length - 1)
  const dotY = py(values[values.length - 1])
  return (
    <svg width={W} height={H} aria-label="price sparkline" style={{ display: 'block', overflow: 'visible', margin: '0 auto' }}>
      <rect x={0} y={0} width={W} height={H} rx={6} fill={fillColor} />
      <path d={fillD} fill={fillColor} />
      <path d={d} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={dotX} cy={dotY} r="3.5" fill={color} stroke="white" strokeWidth="1.5" />
    </svg>
  )
}

// ---------------------------------------------------------------------------
// Upload Modal
// ---------------------------------------------------------------------------
interface UploadModalProps {
  onClose: () => void
  onAdd: (holdings: Holding[]) => void
  userId: string
}

type ModalStep = 'upload' | 'preview' | 'saving'

function UploadModal({ onClose, onAdd, userId }: UploadModalProps) {
  const [step, setStep] = useState<ModalStep>('upload')
  const [dragging, setDragging] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [rows, setRows] = useState<EditableHolding[]>([])
  const [editingIdx, setEditingIdx] = useState<number | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const handleFile = useCallback(async (file: File) => {
    const ALLOWED_EXTS = ['.pdf', '.xlsx', '.xls']
    const fname = file.name.toLowerCase()
    if (!ALLOWED_EXTS.some(ext => fname.endsWith(ext))) { setError('Please upload a PDF, XLSX, or XLS file.'); return }
    setError(null); setLoading(true)

    // Attach the session JWT so the backend can verify which user is uploading
    const { data: sessionData } = await supabase.auth.getSession()
    const token = sessionData?.session?.access_token
    if (!token) {
      setError('You must be signed in to upload a file.')
      setLoading(false)
      return
    }

    const formData = new FormData()
    formData.append('file', file)
    try {
      const resp = await fetch(`${BACKEND_URL}/api/portfolio/upload-pdf`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: 'Unknown error' }))
        throw new Error(err.detail || `HTTP ${resp.status}`)
      }
      const data = await resp.json()
      const extracted: Holding[] = data.holdings || []
      if (extracted.length === 0) setError('No holdings extracted. Add them manually below.')
      setRows(extracted.map((h, i) => ({ ...h, _rowKey: `row-${Date.now()}-${i}` })))
      setStep('preview')
    } catch (e: any) {
      setRows([])
      setStep('preview')
      setError(`Backend unavailable (${e.message}). Add holdings manually below.`)
    } finally {
      setLoading(false)
    }
  }, [])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }, [handleFile])

  const updateRow = (idx: number, field: keyof Holding, value: string) => {
    setRows(prev => {
      const next = [...prev]
      const row = { ...next[idx] }
      if (field === 'units' || field === 'buy_price' || field === 'current_price') {
        (row as any)[field] = value === '' ? null : parseFloat(value)
      } else {
        (row as any)[field] = value
      }
      next[idx] = row
      return next
    })
  }

  const deleteRow = (idx: number) => setRows(prev => prev.filter((_, i) => i !== idx))

  const addEmptyRow = () => {
    setRows(prev => [...prev, {
      _rowKey: `row-${Date.now()}-new`,
      name: '', symbol: '', isin: '', type: 'STOCK',
      buy_date: null, units: null, buy_price: null, current_price: null,
    }])
    setEditingIdx(rows.length)
  }

  const handleAdd = async () => {
    const valid = rows.filter(r => r.name.trim())
    if (valid.length === 0) { setError('Add at least one holding with a name.'); return }
    // A holding cannot be valued without a quantity and a buy price, and
    // guessing either would put invented numbers in the portfolio.
    const incomplete = valid.filter(r => !(Number(r.units) > 0) || r.buy_price == null || Number(r.buy_price) < 0)
    if (incomplete.length > 0) {
      const names = incomplete.slice(0, 3).map(r => r.name.trim()).join(', ')
      const more = incomplete.length > 3 ? ` and ${incomplete.length - 3} more` : ''
      setError(`Enter the units and buy price for: ${names}${more}. Or remove ${incomplete.length === 1 ? 'that row' : 'those rows'}.`)
      return
    }
    setStep('saving'); setError(null)
    const insertRows = valid.map(h => ({
      user_id: userId,
      name: h.name.trim(),
      symbol: h.symbol || '',
      isin: h.isin || '',
      type: h.type || 'STOCK',
      buy_date: h.buy_date || null,
      units: h.units,
      buy_price: h.buy_price,
      current_price: h.current_price != null && h.current_price > 0 ? h.current_price : null,
    }))
    try {
      const { data, error: sbErr } = await supabase.from('portfolio_holdings').insert(insertRows).select()
      if (sbErr) throw new Error(sbErr.message)
      onAdd(data as Holding[])
    } catch (e: any) {
      setError(e.message || 'Failed to save holdings.')
      setStep('preview')
    }
  }

  const STEPS: ModalStep[] = ['upload', 'preview', 'saving']

  return (
    <div
      id="upload-modal-overlay"
      className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4"
      style={{ background: 'rgba(15,15,35,0.65)', backdropFilter: 'blur(6px)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        id="upload-modal"
        className="relative w-full max-w-4xl bg-white rounded-2xl shadow-2xl overflow-hidden flex flex-col"
        style={{ maxHeight: '90vh' }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-6 py-4 border-b border-white/10"
          style={{ background: 'linear-gradient(135deg, #00B386 0%, #007A5A 100%)' }}
        >
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-white/20 flex items-center justify-center">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
                <line x1="12" y1="18" x2="12" y2="12"/>
                <line x1="9" y1="15" x2="15" y2="15"/>
              </svg>
            </div>
            <div>
              <h2 className="text-white font-bold text-base">Add Latest PDF</h2>
              <p className="text-white/70 text-xs">
                {step === 'upload' ? 'Upload PDF or Excel (.xlsx/.xls) broker statement' :
                 step === 'preview' ? `${rows.length} holding${rows.length !== 1 ? 's' : ''} extracted — review & edit` :
                 'Saving to your portfolio…'}
              </p>
            </div>
          </div>
          <button id="upload-modal-close" onClick={onClose} className="w-8 h-8 rounded-lg bg-white/20 hover:bg-white/30 flex items-center justify-center transition-colors">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>

        {/* Step indicator */}
        <div className="flex items-center px-6 pt-4 pb-1 gap-1">
          {STEPS.map((s, i) => (
            <React.Fragment key={s}>
              <div className={`flex items-center gap-1.5 text-xs font-medium transition-colors ${step === s ? 'text-groww-green' : i < STEPS.indexOf(step) ? 'text-groww-green/60' : 'text-gray-400'}`}>
                <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold ${step === s ? 'bg-groww-green text-white' : i < STEPS.indexOf(step) ? 'bg-groww-green/20 text-groww-green' : 'bg-gray-100 text-gray-400'}`}>
                  {i + 1}
                </div>
                <span className="capitalize">{s}</span>
              </div>
              {i < STEPS.length - 1 && <div className="flex-1 h-px bg-gray-200 mx-2 max-w-[60px]" />}
            </React.Fragment>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto">
          {error && (
            <div className="mx-6 mt-4 px-4 py-3 rounded-xl bg-red-50 border border-red-100 text-red-600 text-sm flex items-center gap-2">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
              </svg>
              {error}
            </div>
          )}

          {/* Upload Step */}
          {step === 'upload' && (
            <div className="p-6">
              <div
                id="pdf-dropzone"
                className={`relative border-2 border-dashed rounded-2xl p-12 flex flex-col items-center justify-center cursor-pointer transition-all duration-200 ${dragging ? 'border-groww-green bg-green-50' : 'border-gray-200 hover:border-groww-green hover:bg-green-50/30'}`}
                onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
                onClick={() => fileRef.current?.click()}
              >
                <input ref={fileRef} type="file" accept=".pdf,.xlsx,.xls" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f) }} id="pdf-file-input" />
                {loading ? (
                  <div className="flex flex-col items-center gap-3">
                    <div className="w-12 h-12 border-4 border-groww-green/20 border-t-groww-green rounded-full animate-spin" />
                    <p className="text-sm text-gray-500 font-medium">Extracting holdings from file…</p>
                    <p className="text-xs text-gray-400">This may take a few seconds</p>
                  </div>
                ) : (
                  <>
                    <div className="w-16 h-16 rounded-2xl flex items-center justify-center mb-4" style={{ background: dragging ? 'rgba(0,179,134,0.15)' : '#F0FAF7' }}>
                      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#00B386" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                        <polyline points="17 8 12 3 7 8"/>
                        <line x1="12" y1="3" x2="12" y2="15"/>
                      </svg>
                    </div>
                    <p className="text-base font-semibold text-gray-700 mb-1">{dragging ? 'Drop your file here' : 'Drag & drop your PDF or Excel file'}</p>
                    <p className="text-sm text-gray-400 mb-4">or click to browse</p>
                    <div className="flex items-center gap-5 text-xs text-gray-400 flex-wrap justify-center">
                      {['PDF', 'Excel .xlsx/.xls', 'CDSL CAS', 'Zerodha', 'Groww'].map(b => (
                        <span key={b} className="flex items-center gap-1">
                          <span className="w-1.5 h-1.5 rounded-full bg-groww-green inline-block"/>
                          {b}
                        </span>
                      ))}
                    </div>
                  </>
                )}
              </div>
              <p className="text-xs text-gray-400 text-center mt-4 flex items-center justify-center gap-1">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                </svg>
                Your file is processed in-memory and never permanently stored on our servers.
              </p>
            </div>
          )}

          {/* Preview Step */}
          {step === 'preview' && (
            <div className="p-6">
              <div className="flex items-center justify-between mb-4">
                <p className="text-sm text-gray-500">Review extracted data. Click any field to edit it.</p>
                <button
                  id="add-empty-row-btn"
                  onClick={addEmptyRow}
                  className="flex items-center gap-1.5 text-xs font-medium text-groww-green hover:text-groww-green-dark px-3 py-1.5 rounded-lg hover:bg-green-50 transition-colors"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                  </svg>
                  Add row manually
                </button>
              </div>

              <div className="border border-gray-200 rounded-xl overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] text-sm">
                    <thead>
                      <tr className="bg-gray-50 border-b border-gray-200">
                        {['Name *', 'Symbol', 'Type', 'Buy Date', 'Units', 'Buy Price', 'Live Price', ''].map(h => (
                          <th key={h} className="text-left px-3 py-2.5 text-xs font-semibold text-gray-500 whitespace-nowrap">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.length === 0 ? (
                        <tr>
                          <td colSpan={8} className="text-center py-10 text-gray-400 text-sm">
                            No data extracted. Use "Add row manually" to add holdings.
                          </td>
                        </tr>
                      ) : rows.map((row, idx) => (
                        <tr
                          key={row._rowKey}
                          className={`border-b border-gray-100 last:border-0 transition-colors ${editingIdx === idx ? 'bg-green-50/60' : 'hover:bg-gray-50'}`}
                          onClick={() => setEditingIdx(idx)}
                        >
                          <td className="px-3 py-2">
                            <input className="w-full min-w-[130px] bg-transparent outline-none text-gray-800 font-medium placeholder-gray-300 focus:border-b border-groww-green px-1"
                              value={row.name} onChange={e => updateRow(idx, 'name', e.target.value)}
                              placeholder="e.g. Infosys Ltd" onClick={e => e.stopPropagation()} />
                          </td>
                          <td className="px-3 py-2">
                            <input className="w-full min-w-[80px] bg-transparent outline-none text-gray-600 placeholder-gray-300 px-1"
                              value={row.symbol} onChange={e => updateRow(idx, 'symbol', e.target.value)}
                              placeholder="INFY" onClick={e => e.stopPropagation()} />
                          </td>
                          <td className="px-3 py-2">
                            <select className="bg-transparent outline-none text-gray-600 text-xs rounded px-1 cursor-pointer"
                              value={row.type} onChange={e => updateRow(idx, 'type', e.target.value)}
                              onClick={e => e.stopPropagation()}>
                              <option>STOCK</option><option>MF</option><option>ETF</option><option>COMMODITY</option><option>BOND</option>
                            </select>
                          </td>
                          <td className="px-3 py-2">
                            <input type="date" className="bg-transparent outline-none text-gray-600 text-xs px-1 min-w-[110px]"
                              value={row.buy_date || ''} onChange={e => updateRow(idx, 'buy_date', e.target.value)}
                              onClick={e => e.stopPropagation()} />
                          </td>
                          <td className="px-3 py-2">
                            <input type="number" className="w-full min-w-[70px] bg-transparent outline-none text-gray-600 px-1"
                              value={row.units ?? ''} onChange={e => updateRow(idx, 'units', e.target.value)}
                              placeholder="0" onClick={e => e.stopPropagation()} />
                          </td>
                          <td className="px-3 py-2">
                            <input type="number" className="w-full min-w-[90px] bg-transparent outline-none text-gray-600 px-1"
                              value={row.buy_price ?? ''} onChange={e => updateRow(idx, 'buy_price', e.target.value)}
                              placeholder="0.00" onClick={e => e.stopPropagation()} />
                          </td>
                          <td className="px-3 py-2">
                            <input type="number" className="w-full min-w-[90px] bg-transparent outline-none text-gray-600 px-1"
                              value={row.current_price ?? ''} onChange={e => updateRow(idx, 'current_price', e.target.value)}
                              placeholder="Optional" onClick={e => e.stopPropagation()} />
                          </td>
                          <td className="px-3 py-2">
                            <button onClick={e => { e.stopPropagation(); deleteRow(idx) }}
                              className="w-6 h-6 rounded-md flex items-center justify-center text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors" title="Remove row">
                              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                              </svg>
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <p className="text-xs text-gray-400 mt-3 flex items-center gap-1">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
                </svg>
                Fields marked * are required. Live prices can be updated later.
              </p>
            </div>
          )}

          {/* Saving Step */}
          {step === 'saving' && (
            <div className="p-12 flex flex-col items-center justify-center gap-4">
              <div className="w-14 h-14 border-4 border-groww-green/20 border-t-groww-green rounded-full animate-spin" />
              <p className="text-base font-semibold text-gray-700">Saving your holdings…</p>
              <p className="text-sm text-gray-400">Just a moment</p>
            </div>
          )}
        </div>

        {/* Footer */}
        {step !== 'saving' && (
          <div className="px-6 py-4 border-t border-gray-100 flex items-center justify-between bg-white">
            {step === 'preview' ? (
              <>
                <button id="modal-back-btn" onClick={() => { setStep('upload'); setRows([]); setError(null) }}
                  className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium text-gray-600 hover:bg-gray-100 transition-colors">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="15 18 9 12 15 6"/>
                  </svg>
                  Back
                </button>
                <div className="flex items-center gap-3">
                  <button id="modal-cancel-btn" onClick={onClose}
                    className="px-5 py-2 rounded-xl text-sm font-medium text-gray-600 border border-gray-200 hover:bg-gray-50 transition-colors">
                    Cancel
                  </button>
                  <button
                    id="modal-add-btn"
                    onClick={handleAdd}
                    disabled={rows.filter(r => r.name.trim()).length === 0}
                    className="px-6 py-2 rounded-xl text-sm font-semibold text-white transition-all duration-200 flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed hover:-translate-y-0.5 active:translate-y-0"
                    style={{ background: 'linear-gradient(135deg, #00B386, #007A5A)' }}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12"/>
                    </svg>
                    Add {rows.filter(r => r.name.trim()).length} Holding{rows.filter(r => r.name.trim()).length !== 1 ? 's' : ''}
                  </button>
                </div>
              </>
            ) : (
              <button id="modal-cancel-upload-btn" onClick={onClose}
                className="px-5 py-2 rounded-xl text-sm font-medium text-gray-600 border border-gray-200 hover:bg-gray-50 transition-colors">
                Cancel
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Portfolio Page
// ---------------------------------------------------------------------------
interface PortfolioPageProps {
  /** When true, externally triggers the upload modal to open (e.g. from Sidebar). */
  externalShowModal?: boolean
  /** Called after the external trigger has been consumed so the parent resets its flag. */
  onExternalModalClose?: () => void
}

const PortfolioPage: React.FC<PortfolioPageProps> = ({ externalShowModal, onExternalModalClose }) => {
  const { user } = useAuth()
  const [holdings, setHoldings] = useState<Holding[]>([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [filterType, setFilterType] = useState<string>('ALL')
  const [sortBy, setSortBy] = useState<'name' | 'pnl' | 'value'>('name')
  const [error, setError] = useState<string | null>(null)
  const [livePrices, setLivePrices] = useState<Record<string, number>>({})
  const [liveTickers, setLiveTickers] = useState<Record<string, string>>({})
  const [pricesAsOf, setPricesAsOf] = useState<string | null>(null)
  const [pricesFailed, setPricesFailed] = useState(false)
  const [priceHistory, setPriceHistory] = useState<Record<string, number[]>>({})

  const fetchHoldings = useCallback(async () => {
    if (!user?.id) return
    setLoading(true); setError(null)
    try {
      const { data, error: sbErr } = await supabase
        .from('portfolio_holdings').select('*')
        .eq('user_id', user.id).order('created_at', { ascending: false })
      if (sbErr) throw sbErr
      setHoldings((data as Holding[]) || [])
    } catch (e: any) {
      setError(e.message || 'Failed to load portfolio.')
    } finally {
      setLoading(false)
    }
  }, [user?.id])

  useEffect(() => { fetchHoldings() }, [fetchHoldings])

  // Respond to external trigger (e.g. "Add Latest PDF" clicked in Sidebar)
  useEffect(() => {
    if (externalShowModal) {
      setShowModal(true)
      onExternalModalClose?.()  // reset parent flag so it doesn't re-fire
    }
  }, [externalShowModal, onExternalModalClose])

  // Latest market price for every holding, refreshed each minute. The backend
  // finds each one by symbol, then ISIN, then company name.
  const priceKey = (h: Holding) => h.id ?? `${h.symbol}|${h.isin}|${h.name}`
  const refsJson = JSON.stringify(
    holdings.map(h => ({ key: priceKey(h), symbol: h.symbol ?? '', isin: h.isin ?? '', name: h.name ?? '' })),
  )

  // Fetch 30d history for sparklines
  const historyRefsJson = JSON.stringify(
    holdings
      .filter(h => !isLeftover(h))
      .map(h => ({ name: h.name, symbol: h.symbol, isin: h.isin, units: h.units, buy_price: h.buy_price, type: h.type }))
  )
  useEffect(() => {
    if (historyRefsJson === '[]') return
    let cancelled = false
    const load = async () => {
      try {
        const payload = { holdings: JSON.parse(historyRefsJson), range: '1y' }
        console.log('Fetching Portfolio history with payload:', payload)
        const resp = await fetch(`${BACKEND_URL}/api/insights/portfolio`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
        if (!resp.ok) {
          console.error('Portfolio history fetch failed with status:', resp.status)
          return
        }
        const json = await resp.json()
        if (cancelled) return
        const series = json?.prices?.series ?? []
        const newHistory: Record<string, number[]> = {}
        series.forEach((s: any) => {
          const nums = (s.values || []).filter((v: any): v is number => v !== null)
          newHistory[s.ticker] = nums.slice(-30)
        })
        console.log('Portfolio history received and mapped:', newHistory)
        setPriceHistory(newHistory)
      } catch (err) {
        console.error('Error fetching portfolio history:', err)
      }
    }
    load()
    return () => { cancelled = true }
  }, [historyRefsJson])

  useEffect(() => {
    if (refsJson === '[]') return
    let cancelled = false
    const load = async () => {
      try {
        const resp = await fetch(`${BACKEND_URL}/api/prices`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: `{"holdings":${refsJson}}`,
        })
        if (!resp.ok) throw new Error(`Request failed (${resp.status})`)
        const data = await resp.json()
        if (cancelled) return
        setLivePrices(data.prices ?? {})
        setLiveTickers(data.tickers ?? {})
        setPricesAsOf(data.as_of ?? null)
        setPricesFailed(false)
      } catch {
        // Keep the last prices on screen; only flag the failure.
        if (!cancelled) setPricesFailed(true)
      }
    }
    load()
    const timer = setInterval(load, PRICE_REFRESH_MS)
    return () => { cancelled = true; clearInterval(timer) }
  }, [refsJson])

  const isLive = (h: Holding) => livePrices[priceKey(h)] != null
  const priced = holdings.map(h => ({
    ...h,
    current_price: isLive(h) ? livePrices[priceKey(h)] : savedPrice(h),
  }))
  const unpricedCount = priced.filter(h => h.current_price == null && !isLeftover(h)).length
  const leftovers = holdings.filter(h => h.id && isLeftover(h))

  const removeLeftovers = async () => {
    const ids = leftovers.map(h => h.id!)
    const { error: sbErr } = await supabase.from('portfolio_holdings').delete().in('id', ids)
    if (sbErr) { setError(sbErr.message); return }
    setHoldings(prev => prev.filter(h => !ids.includes(h.id ?? '')))
  }
  const liveCount = holdings.filter(isLive).length

  const handleAdd = (newHoldings: Holding[]) => {
    setHoldings(prev => [...newHoldings, ...prev])
    setShowModal(false)
  }

  const handleDelete = async (id: string) => {
    if (!confirm('Remove this holding from your portfolio?')) return
    const { error: sbErr } = await supabase.from('portfolio_holdings').delete().eq('id', id)
    if (!sbErr) setHoldings(prev => prev.filter(h => h.id !== id))
  }

  const displayed = priced
    .filter(h => filterType === 'ALL' || h.type === filterType)
    .sort((a, b) => {
      if (sortBy === 'name') return a.name.localeCompare(b.name)
      if (sortBy === 'pnl')  return (calcPnL(b) ?? 0) - (calcPnL(a) ?? 0)
      const av = (a.units ?? 0) * (a.current_price ?? a.buy_price ?? 0)
      const bv = (b.units ?? 0) * (b.current_price ?? b.buy_price ?? 0)
      return bv - av
    })

  const totalInvested = holdings.reduce((s, h) => s + (h.units ?? 0) * (h.buy_price ?? 0), 0)
  const totalCurrent  = priced.reduce((s, h) => s + (h.units ?? 0) * (h.current_price ?? h.buy_price ?? 0), 0)
  const totalPnL      = totalCurrent - totalInvested
  const totalPnLPct   = totalInvested > 0 ? (totalPnL / totalInvested) * 100 : 0

  return (
    <div id="portfolio-page" className="flex flex-col min-h-full">
      {/* Header */}
      <header
        id="portfolio-header"
        className="sticky top-0 z-10 bg-white border-b border-groww-border-light px-4 sm:px-6 py-3 sm:py-4 flex items-center justify-between gap-3"
        style={{ minHeight: '60px' }}
      >
        <div>
          <h1 className="text-lg font-bold text-groww-text-primary">Portfolio</h1>
          <p className="text-xs text-groww-text-muted mt-0.5">
            {holdings.length} holding{holdings.length !== 1 ? 's' : ''} &middot; {new Date().toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })}
            {pricesAsOf && (
              <span id="portfolio-live-status">
                {' '}&middot; {liveCount} of {holdings.length} live, updated {new Date(pricesAsOf).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
              </span>
            )}
            {pricesFailed && <span className="text-amber-600"> &middot; live prices unavailable, showing {pricesAsOf ? 'the last update' : 'saved prices'}</span>}
          </p>
        </div>
        <button
          id="add-pdf-btn"
          onClick={() => setShowModal(true)}
          className="shrink-0 flex items-center gap-2 px-3 sm:px-4 py-2 sm:py-2.5 rounded-xl text-xs sm:text-sm font-semibold text-white whitespace-nowrap transition-all duration-200 hover:-translate-y-0.5 active:translate-y-0"
          style={{ background: 'linear-gradient(135deg, #00B386 0%, #007A5A 100%)', boxShadow: '0 4px 14px rgba(0,179,134,0.3)' }}
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
            <polyline points="14 2 14 8 20 8"/>
            <line x1="12" y1="18" x2="12" y2="12"/>
            <line x1="9" y1="15" x2="15" y2="15"/>
          </svg>
          Add Latest PDF
        </button>
      </header>

      <div className="flex-1 p-4 sm:p-6">
        {error && <div className="mb-4 px-4 py-3 rounded-xl bg-red-50 border border-red-100 text-red-600 text-sm">{error}</div>}

        {leftovers.length > 0 && (
          <div id="portfolio-leftovers" className="mb-4 px-4 py-3 rounded-xl bg-amber-50 border border-amber-100 text-amber-800 text-sm flex flex-wrap items-center gap-3">
            <p className="flex-1 min-w-[220px]">
              {leftovers.length} row{leftovers.length !== 1 ? 's' : ''} came from a statement's header, not from holdings:{' '}
              <span className="font-medium">{leftovers.map(h => h.name).join(', ')}</span>.
            </p>
            <button
              onClick={removeLeftovers}
              className="shrink-0 px-3 py-1.5 rounded-lg bg-amber-600 text-white text-xs font-semibold hover:bg-amber-700 transition-colors"
            >
              Remove {leftovers.length === 1 ? 'it' : `all ${leftovers.length}`}
            </button>
          </div>
        )}

        {unpricedCount > 0 && (
          <p id="portfolio-unpriced" className="mb-4 px-4 py-2.5 rounded-xl bg-gray-50 border border-gray-100 text-gray-600 text-xs">
            {unpricedCount} holding{unpricedCount !== 1 ? 's have' : ' has'} no market price. {unpricedCount !== 1 ? 'They are' : 'It is'} counted
            at cost in Current Value, and {unpricedCount !== 1 ? 'their' : 'its'} profit or loss is not shown.
          </p>
        )}

        {/* Summary Cards */}
        {holdings.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
            {[
              { label: 'Total Invested', value: fmtCur(totalInvested), icon: '💰', color: '#6366F1' },
              { label: 'Current Value',  value: fmtCur(totalCurrent),  icon: '📊', color: '#00B386' },
              {
                label: 'Total P&L',
                value: `${totalPnL >= 0 ? '+' : ''}${fmtCur(totalPnL)} (${totalPnL >= 0 ? '+' : ''}${totalPnLPct.toFixed(2)}%)`,
                icon: totalPnL >= 0 ? '📈' : '📉',
                color: totalPnL >= 0 ? '#16A34A' : '#DC2626',
              },
            ].map(card => (
              <div key={card.label} className="bg-white rounded-2xl p-5 border border-gray-100 shadow-sm hover:shadow-md transition-shadow">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xl">{card.icon}</span>
                  <span className="text-xs font-medium text-gray-500">{card.label}</span>
                </div>
                <p className="text-xl font-bold" style={{ color: card.color }}>{card.value}</p>
              </div>
            ))}
          </div>
        )}

        {/* Filters + Sort */}
        {holdings.length > 0 && (
          <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
            <div className="flex items-center gap-2 flex-wrap">
              {(['ALL', 'STOCK', 'MF', 'ETF', 'COMMODITY', 'BOND'] as const).map(t => (
                <button key={t} onClick={() => setFilterType(t)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${filterType === t ? 'bg-groww-green text-white shadow-sm' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'}`}>
                  {t}
                </button>
              ))}
            </div>
            <select value={sortBy} onChange={e => setSortBy(e.target.value as any)}
              className="text-xs text-gray-600 bg-white border border-gray-200 rounded-lg px-3 py-1.5 outline-none cursor-pointer focus:border-groww-green">
              <option value="name">Sort: Name</option>
              <option value="pnl">Sort: P&L</option>
              <option value="value">Sort: Value</option>
            </select>
          </div>
        )}

        {/* Holdings Table */}
        {loading ? (
          <div className="flex flex-col items-center justify-center py-24 gap-4">
            <div className="w-10 h-10 border-4 border-groww-green/20 border-t-groww-green rounded-full animate-spin" />
            <p className="text-sm text-gray-400">Loading your portfolio…</p>
          </div>
        ) : displayed.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 text-center">
            <div className="w-20 h-20 rounded-2xl flex items-center justify-center mb-5" style={{ background: 'linear-gradient(135deg, #E8F5F1, #F0FAF7)' }}>
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#00B386" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>
              </svg>
            </div>
            <h2 className="text-lg font-bold text-gray-700 mb-2">{holdings.length === 0 ? 'No holdings yet' : 'No holdings match the filter'}</h2>
            <p className="text-sm text-gray-400 max-w-xs mb-6">
              {holdings.length === 0 ? 'Upload your broker PDF or Excel file to auto-import your portfolio.' : 'Try selecting a different asset type.'}
            </p>
            {holdings.length === 0 && (
              <button onClick={() => setShowModal(true)}
                className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold text-white transition-all hover:-translate-y-0.5"
                style={{ background: 'linear-gradient(135deg, #00B386, #007A5A)', boxShadow: '0 4px 14px rgba(0,179,134,0.3)' }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
                </svg>
                Add Latest PDF
              </button>
            )}
          </div>
        ) : (
          <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px]">
                <thead>
                  <tr className="bg-gray-50 border-b border-gray-100">
                    <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Stock</th>
                    <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500">Type</th>
                    <th className="text-right px-4 py-3 text-xs font-semibold text-gray-500">Units</th>
                    <th className="text-right px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Buy Price</th>
                    <th className="text-right px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Live Rate</th>
                    <th className="text-center px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">30d Trend</th>
                    <th className="text-right px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Invested</th>
                    <th className="text-right px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">P&amp;L</th>
                    <th className="px-4 py-3" />
                  </tr>
                </thead>
                <tbody>
                  {displayed.map((h, i) => {
                    const pnl      = calcPnL(h)
                    const pnlP     = pnlPct(h)
                    const isProfit = pnl != null && pnl >= 0
                    const invested = (h.units ?? 0) * (h.buy_price ?? 0)
                    return (
                      <tr key={h.id || i} className="border-b border-gray-50 last:border-0 hover:bg-gray-50/60 transition-colors group">
                        <td className="px-5 py-3.5">
                          <div className="flex items-center gap-3">
                            <StockLogo name={h.name} symbol={h.symbol} />
                            <div>
                              <p className="text-sm font-semibold text-gray-800 leading-tight">{h.name}</p>
                              {(() => {
                                // The trading symbol, or the ticker the price was matched to. Never the ISIN.
                                const ticker = h.symbol?.trim() || (liveTickers[priceKey(h)] ?? '').replace(/\.(NS|BO)$/, '')
                                return ticker ? <p className="text-xs text-gray-400">{ticker}</p> : null
                              })()}
                            </div>
                          </div>
                        </td>
                        <td className="px-4 py-3.5">
                          <span className={`inline-block px-2 py-0.5 rounded-md text-[10px] font-bold tracking-wide ${TYPE_COLOURS[h.type] || 'bg-gray-100 text-gray-600'}`}>
                            {h.type}
                          </span>
                        </td>
                        <td className="px-4 py-3.5 text-right text-sm text-gray-700 tabular-nums">{fmt(h.units, 3)}</td>
                        <td className="px-4 py-3.5 text-right text-sm text-gray-700 tabular-nums">{fmtCur(h.buy_price)}</td>
                        <td className="px-4 py-3.5 text-right">
                          {h.current_price != null ? (
                            <div className="flex flex-col items-end">
                              <span className="text-sm font-medium text-gray-800 tabular-nums">{fmtCur(h.current_price)}</span>
                              {isLive(h) ? (
                                <span
                                  className="flex items-center gap-1 text-[10px] font-medium text-groww-green"
                                  title={`Latest market price for ${liveTickers[priceKey(h)] ?? 'this holding'}`}
                                >
                                  <span className="w-1.5 h-1.5 rounded-full bg-groww-green" />
                                  Live &middot; {(liveTickers[priceKey(h)] ?? '').replace(/\.(NS|BO)$/, '')}
                                </span>
                              ) : (
                                <span className="text-[10px] text-gray-400" title="No market price found for this holding; showing the price saved with it">Saved</span>
                              )}
                            </div>
                          ) : <span className="text-xs text-gray-400 italic" title="No market price was found for this holding">No price</span>}
                        </td>
                        <td className="px-4 py-3.5 text-center">
                          {(() => {
                            const raw = h.symbol?.trim() || liveTickers[priceKey(h)] || ''
                            const t = raw.toUpperCase()
                            const tBase = t.replace(/\.(NS|BO)$/, '')
                            const vals = priceHistory[t] || priceHistory[`${tBase}.NS`] || priceHistory[`${tBase}.BO`] || priceHistory[tBase] || []
                            const trendIsUp = vals.length >= 2 ? vals[vals.length - 1] >= vals[0] : true
                            return <MiniSparkline values={vals} positive={trendIsUp} />
                          })()}
                        </td>
                        <td className="px-4 py-3.5 text-right text-sm text-gray-600 tabular-nums">{fmtCur(invested)}</td>
                        <td className="px-4 py-3.5 text-right">
                          {pnl != null ? (
                            <div className={`flex flex-col items-end ${isProfit ? 'text-green-600' : 'text-red-500'}`}>
                              <span className="text-sm font-semibold tabular-nums">{isProfit ? '+' : ''}{fmtCur(pnl)}</span>
                              <span className="text-[10px] font-medium tabular-nums opacity-80">{isProfit ? '▲' : '▼'} {Math.abs(pnlP ?? 0).toFixed(2)}%</span>
                            </div>
                          ) : <span className="text-xs text-gray-400">—</span>}
                        </td>
                        <td className="px-4 py-3.5">
                          {h.id && (
                            <button onClick={() => handleDelete(h.id!)}
                              className="w-7 h-7 rounded-lg flex items-center justify-center text-gray-300 hover:text-red-400 hover:bg-red-50 opacity-0 group-hover:opacity-100 transition-all" title="Remove holding">
                              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="3 6 5 6 21 6"/>
                                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                                <path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/>
                              </svg>
                            </button>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {showModal && user && (
        <UploadModal onClose={() => setShowModal(false)} onAdd={handleAdd} userId={user.id} />
      )}
    </div>
  )
}

export default PortfolioPage
