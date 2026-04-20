"use client"

import { useRouter } from "next/navigation"
import { useAuth } from "@/components/auth-provider"
import { Button } from "@/components/ui/button"
import { createTicket } from "@/lib/tickets"
import { Bot, Plus, Server, Shield, Database, Cloud } from "lucide-react"

const features = [
  { icon: Server, label: "Server Troubleshooting", desc: "Linux/Windows server diagnostics" },
  { icon: Shield, label: "Security Response", desc: "Vulnerability fixes & intrusion detection" },
  { icon: Database, label: "Database Operations", desc: "MySQL/PG/Redis support" },
  { icon: Cloud, label: "Cloud Services", desc: "AWS/Azure/GCP issue handling" },
]

export default function ChatPage() {
  const { user } = useAuth()
  const router = useRouter()

  function handleNewTicket() {
    if (!user) return
    const ticket = createTicket(user.id)
    router.push(`/chat/${ticket.id}`)
  }

  return (
    <div className="flex h-[calc(100svh-48px)] flex-col items-center justify-center p-6 md:h-svh">
      <div className="flex max-w-lg flex-col items-center text-center">
        <div className="flex h-20 w-20 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          <Bot className="h-10 w-10" />
        </div>
        <h1 className="mt-6 text-2xl font-bold tracking-tight text-foreground text-balance">
          Welcome to IT Support
        </h1>
        <p className="mt-2 text-muted-foreground text-pretty">
          Your AI operations assistant, ready to help with server troubleshooting, security incidents, performance optimization, and more. Start a new session to begin.
        </p>

        <div className="mt-8 grid w-full grid-cols-2 gap-3">
          {features.map((f) => (
            <div
              key={f.label}
              className="flex flex-col items-center gap-2 rounded-xl border bg-card p-4 text-card-foreground"
            >
              <f.icon className="h-5 w-5 text-primary" />
              <span className="text-sm font-medium">{f.label}</span>
              <span className="text-xs text-muted-foreground">{f.desc}</span>
            </div>
          ))}
        </div>

        <Button onClick={handleNewTicket} size="lg" className="mt-8 gap-2">
          <Plus className="h-4 w-4" />
          New Session
        </Button>
      </div>
    </div>
  )
}
