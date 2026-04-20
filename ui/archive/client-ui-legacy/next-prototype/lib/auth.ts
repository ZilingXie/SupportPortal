import { DEMO_USERS } from "./constants"

export interface User {
  id: string
  name: string
  email: string
}

const AUTH_KEY = "helpdesk_auth_user"

function isValidStoredUser(value: unknown): value is User {
  if (!value || typeof value !== "object") return false
  const user = value as User
  return DEMO_USERS.some(
    (demoUser) =>
      demoUser.id === user.id &&
      demoUser.name === user.name &&
      demoUser.email === user.email
  )
}

export function login(username: string, password: string): User | null {
  const user = DEMO_USERS.find(
    (u) => u.username.toLowerCase() === username.trim().toLowerCase() && u.password === password
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
    const parsed = JSON.parse(stored)
    if (!isValidStoredUser(parsed)) {
      localStorage.removeItem(AUTH_KEY)
      return null
    }
    return parsed as User
  } catch {
    localStorage.removeItem(AUTH_KEY)
    return null
  }
}
