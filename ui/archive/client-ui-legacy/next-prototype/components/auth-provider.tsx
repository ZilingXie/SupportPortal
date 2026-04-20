"use client"

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from "react"
import { getCurrentUser, login as authLogin, logout as authLogout, type User } from "@/lib/auth"
import { useRouter } from "next/navigation"

interface AuthContextType {
  user: User | null
  isLoading: boolean
  login: (email: string, password: string) => User | null
  logout: () => void
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  isLoading: true,
  login: () => null,
  logout: () => {},
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const router = useRouter()

  useEffect(() => {
    const stored = getCurrentUser()
    setUser(stored)
    setIsLoading(false)
  }, [])

  const login = useCallback(
    (email: string, password: string): User | null => {
      const result = authLogin(email, password)
      if (result) {
        setUser(result)
      }
      return result
    },
    []
  )

  const logout = useCallback(() => {
    authLogout()
    setUser(null)
    router.push("/login")
  }, [router])

  return (
    <AuthContext.Provider value={{ user, isLoading, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
