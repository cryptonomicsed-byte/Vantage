import React from 'react'
import { NavLink, useLocation } from 'react-router-dom'

interface SubNavLink {
  to: string
  label: string
}

export default function SubNav({ links }: { links: SubNavLink[] }) {
  const location = useLocation()

  return (
    <nav className="sub-nav" aria-label="Section navigation">
      {links.map(link => {
        const isAgentsLink = link.to === '/agents'
        const onAgentProfile = location.pathname.startsWith('/agent/')
        const forceActive = isAgentsLink && onAgentProfile

        return (
          <NavLink
            key={link.to}
            to={link.to}
            end
            className={({ isActive }) =>
              'sub-nav-link' + (isActive || forceActive ? ' active' : '')
            }
          >
            {link.label}
          </NavLink>
        )
      })}
    </nav>
  )
}
