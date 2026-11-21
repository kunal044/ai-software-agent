import { useEffect, useRef } from 'react'
import { monaco } from './monaco.js'

export default function DiffView({ original, modified, language = 'python', inlineBreakpoint }) {
  const container = useRef(null)

  useEffect(() => {
    const editor = monaco.editor.createDiffEditor(container.current, {
      theme: 'vs',
      readOnly: true,
      renderSideBySide: true,
      useInlineViewWhenSpaceIsLimited: true,
      ...(inlineBreakpoint ? { renderSideBySideInlineBreakpoint: inlineBreakpoint, diffWordWrap: 'on' } : {}),
      automaticLayout: true,
      minimap: { enabled: false },
      scrollBeyondLastLine: false,
      fontSize: 13,
      originalAriaLabel: 'Original source',
      modifiedAriaLabel: 'Proposed source',
    })
    const originalModel = monaco.editor.createModel(original, language)
    const modifiedModel = monaco.editor.createModel(modified, language)
    editor.setModel({ original: originalModel, modified: modifiedModel })

    return () => {
      editor.setModel(null)
      editor.dispose()
      originalModel.dispose()
      modifiedModel.dispose()
    }
  }, [original, modified, language, inlineBreakpoint])

  return <div ref={container} className="diff-view" />
}


