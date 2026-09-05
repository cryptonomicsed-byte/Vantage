import React from 'react'
export default function ReactMarkdown({ children }) {
  return React.createElement('div', { className: 'markdown' }, children)
}
