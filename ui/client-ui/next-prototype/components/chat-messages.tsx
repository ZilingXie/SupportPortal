"use client"

import { useEffect, useRef } from "react"
import type { UIMessage } from "ai"
import { cn } from "@/lib/utils"
import { Bot, User } from "lucide-react"
import { ScrollArea } from "@/components/ui/scroll-area"

function getMessageText(message: UIMessage): string {
  if (!message.parts || !Array.isArray(message.parts)) return ""
  return message.parts
    .filter((p): p is { type: "text"; text: string } => p.type === "text")
    .map((p) => p.text)
    .join("")
}

function MessageBubble({ message }: { message: UIMessage }) {
  const isUser = message.role === "user"
  const text = getMessageText(message)

  return (
    <div className={cn("flex gap-3", isUser && "flex-row-reverse")}>
      <div
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-muted-foreground"
        )}
      >
        {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
      </div>
      <div
        className={cn(
          "max-w-[75%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-card text-card-foreground border"
        )}
      >
        <div className="whitespace-pre-wrap">{text}</div>
      </div>
    </div>
  )
}

export function ChatMessages({
  messages,
  isLoading,
}: {
  messages: UIMessage[]
  isLoading: boolean
}) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, isLoading])

  return (
    <ScrollArea className="flex-1">
      <div className="flex flex-col gap-4 p-6">
        {messages.length === 0 && (
          <div className="flex flex-1 flex-col items-center justify-center py-20 text-center">
            <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10 text-primary">
              <Bot className="h-8 w-8" />
            </div>
            <h3 className="mt-4 text-lg font-semibold text-foreground">IT Support</h3>
            <p className="mt-1 max-w-sm text-sm text-muted-foreground">
              Describe your IT issue and I will provide professional technical support.
            </p>
          </div>
        )}
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}
        {isLoading && messages[messages.length - 1]?.role === "user" && (
          <div className="flex gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
              <Bot className="h-4 w-4" />
            </div>
            <div className="rounded-2xl border bg-card px-4 py-3">
              <div className="flex gap-1.5">
                <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/40" style={{ animationDelay: "0ms" }} />
                <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/40" style={{ animationDelay: "150ms" }} />
                <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/40" style={{ animationDelay: "300ms" }} />
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  )
}
