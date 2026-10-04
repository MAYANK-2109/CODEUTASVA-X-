import React, { useState } from 'react'

// Method notes and caveats stay one tap away, so a card leads with its result, not its footnotes.
const Notes: React.FC<{ label?: string; children: React.ReactNode; className?: string }> = ({
  label = 'How this is calculated',
  children,
  className = '',
}) => {
  const [open, setOpen] = useState(false)
  return (
    <div className={`notes ${className}`}>
      <button
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="inline-flex items-center gap-1 text-xs font-semibold text-groww-text-secondary hover:text-groww-green transition-colors"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 11v5M12 7.5v.5" />
        </svg>
        {label}
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className={`transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden>
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && <div className="mt-2 flex flex-col gap-1.5 text-xs text-groww-text-secondary leading-relaxed">{children}</div>}
    </div>
  )
}

export default Notes
