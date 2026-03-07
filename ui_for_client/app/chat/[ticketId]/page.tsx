"use client"

import { useState, useEffect, useRef, useCallback } from "react"
import { useParams } from "next/navigation"
import { useChat } from "@ai-sdk/react"
import { DefaultChatTransport } from "ai"
import { useAuth } from "@/components/auth-provider"
import { ChatMessages } from "@/components/chat-messages"
import { ChatInput } from "@/components/chat-input"
import { TicketStatusBadge } from "@/components/ticket-status-badge"
import { Button } from "@/components/ui/button"
import {
  getTicketById,
  updateTicketTitle,
  updateTicketStatus,
  saveTicketMessages,
  type Ticket,
} from "@/lib/tickets"
import type { TicketStatus } from "@/lib/constants"
import { CheckCircle, RotateCcw, SidebarIcon } from "lucide-react"
import { useSidebar } from "@/components/ui/sidebar"
import { toast } from "sonner"

const transport = new DefaultChatTransport({ api: "/api/chat" })

export default function TicketChatPage() {
  const params = useParams()
  const ticketId = params.ticketId as string
  const { user } = useAuth()
  const [ticket, setTicket] = useState<Ticket | null>(null)
  const titleGeneratedRef = useRef(false)
  const { isMobile, toggleSidebar } = useSidebar()
  const [ready, setReady] = useState(false)

  const refreshTicket = useCallback(() => {
    const t = getTicketById(ticketId)
    if (t) setTicket(t)
  }, [ticketId])

  // Load ticket data synchronously before initializing chat
  useEffect(() => {
    refreshTicket()
    titleGeneratedRef.current = false
    setReady(true)
  }, [refreshTicket])

  // Read initial messages from the ticket that was loaded
  const initialMessages = ticket?.messages?.map((m) => ({
    id: m.id,
    role: m.role as "user" | "assistant",
    parts: [{ type: "text" as const, text: m.content }],
  })) || []

  const { messages, sendMessage, status } = useChat({
    id: ticketId,
    transport,
    initialMessages: ready ? initialMessages : [],
    onFinish: () => {
      setTimeout(() => {
        persistMessages()
        updateTicketStatus(ticketId, "waiting_for_agent")
        refreshTicket()
      }, 100)
    },
  })

  const isLoading = status === "streaming" || status === "submitted"

  const persistMessages = useCallback(() => {
    const serialized = messages.map((m) => ({
      id: m.id,
      role: m.role as "user" | "assistant",
      content:
        m.parts
          ?.filter((p): p is { type: "text"; text: string } => p.type === "text")
          .map((p) => p.text)
          .join("") || "",
      createdAt: new Date().toISOString(),
    }))
    saveTicketMessages(ticketId, serialized)
  }, [messages, ticketId])

  useEffect(() => {
    if (messages.length > 0) {
      persistMessages()
    }
  }, [messages, persistMessages])

  async function generateTitle(firstMessage: string) {
    if (titleGeneratedRef.current) return
    titleGeneratedRef.current = true
    try {
      const res = await fetch("/api/generate-title", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: firstMessage }),
      })
      const data = await res.json()
      if (data.title) {
        updateTicketTitle(ticketId, data.title)
        refreshTicket()
      }
    } catch {
      // Silently fail on title generation
    }
  }

  function handleSend(text: string) {
    if (ticket?.status === "resolved") {
      toast.error("This session is closed. Please reopen it to continue.")
      return
    }

    sendMessage({ text })

    if (ticket && (ticket.title === "New Session" || !titleGeneratedRef.current)) {
      generateTitle(text)
    }

    if (ticket?.status === "new") {
      updateTicketStatus(ticketId, "waiting_for_support")
      refreshTicket()
    } else if (ticket?.status === "waiting_for_agent") {
      updateTicketStatus(ticketId, "waiting_for_support")
      refreshTicket()
    }
  }

  function handleResolve() {
    updateTicketStatus(ticketId, "resolved")
    refreshTicket()
    toast.success("Session marked as resolved")
  }

  function handleReopen() {
    updateTicketStatus(ticketId, "waiting_for_support")
    refreshTicket()
    toast.success("Session reopened")
  }

  if (!ticket) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-muted-foreground">Session not found</p>
      </div>
    )
  }

  return (
    <div className="flex h-[calc(100svh-48px)] flex-col md:h-svh">
      {/* Header */}
      <header className="flex items-center justify-between border-b bg-background px-4 py-3 md:px-6">
        <div className="flex items-center gap-3">
          {isMobile && (
            <Button variant="ghost" size="icon" className="h-8 w-8 md:hidden" onClick={toggleSidebar}>
              <SidebarIcon className="h-4 w-4" />
            </Button>
          )}
          <div className="flex flex-col gap-0.5">
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-muted-foreground">{ticket.id}</span>
              <TicketStatusBadge status={ticket.status} />
            </div>
            <h2 className="text-sm font-semibold text-foreground">{ticket.title}</h2>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {ticket.status === "resolved" ? (
            <Button variant="outline" size="sm" onClick={handleReopen} className="gap-1.5">
              <RotateCcw className="h-3.5 w-3.5" />
              Reopen
            </Button>
          ) : (
            <Button variant="outline" size="sm" onClick={handleResolve} className="gap-1.5">
              <CheckCircle className="h-3.5 w-3.5" />
              Resolve
            </Button>
          )}
        </div>
      </header>

      {/* Messages */}
      <ChatMessages messages={messages} isLoading={isLoading} />

      {/* Input */}
      <ChatInput
        onSend={handleSend}
        disabled={isLoading || ticket.status === "resolved"}
      />
    </div>
  )
}
