import { FolderGit2 } from 'lucide-react'

export function Feedback({ error, busy, message = 'Loading…' }) {
  return <>{error && <div className="alert" role="alert">{error}</div>}
    {busy && <p className="muted loading-status" role="status">{message}</p>}</>
}

export function Page({ title, subtitle, action, children }) {
  return <><header className="page-title"><div><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>{action}</header>{children}</>
}

export function Empty({ title, children }) {
  return <div className="empty"><FolderGit2 size={27} /><h3>{title}</h3>{children}</div>
}

