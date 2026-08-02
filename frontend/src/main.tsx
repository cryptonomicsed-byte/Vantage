import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { installApiKeyInterceptor } from './utils/apiKeyInterceptor'

installApiKeyInterceptor()

// Required for the PWA install prompt to be eligible at all -- browsers
// won't fire beforeinstallprompt without a registered SW with a fetch
// handler, regardless of manifest quality. See public/sw.js.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
