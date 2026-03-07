import { DEMO_USERS } from "./constants"

export interface User {
  id: string
  name: string
  email: string
}

const AUTH_KEY = "helpdesk_auth_user"

export function login(email: string, password: string): User | null {
  const user = DEMO_USERS.find(
    (u) => u.email === email && u.password === password
  )
  if (!user) return null
  const userData: User = { id: user.id, name: user.name, email: user.email }
  if (typeof window !== "undefined") {
    localStorage.setItem(AUTH_KEY, JSON.stringify(userData))
  }
  return userData
}

export function logout(): void {
  if (typeof window !== "undefined") {
    localStorage.removeItem(AUTH_KEY)
  }
}

export function getCurrentUser(): User | null {
  if (typeof window === "undefined") return null
  try {
    const stored = localStorage.getItem(AUTH_KEY)
    if (!stored) return null
    return JSON.parse(stored) as User
  } catch {
    return null
  }
}
