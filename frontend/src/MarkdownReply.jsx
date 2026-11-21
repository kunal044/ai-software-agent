import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'

const highlightPlugins = [rehypeHighlight]

export default function MarkdownReply({ text }) {
  if (!text) return null
  return <div className="workspace-markdown">
    <ReactMarkdown skipHtml disallowedElements={['img']} rehypePlugins={highlightPlugins}>{text}</ReactMarkdown>
  </div>
}

