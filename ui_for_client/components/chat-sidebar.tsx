"use client"

import { useRouter, useParams } from "next/navigation"
import { useAuth } from "@/components/auth-provider"
import { TicketStatusBadge } from "@/components/ticket-status-badge"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Monitor, Plus, Clock, LogOut, History } from "lucide-react"
import { getTicketsByUser, createTicket, type Ticket } from "@/lib/tickets"
import { useEffect, useState, useCallback } from "react"

const MAX_RECENT = 5

export function ChatSidebar() {
  const { user, logout } = useAuth()
  const router = useRouter()
  const params = useParams()
  const activeId = params?.ticketId as string | undefined
  const [tickets, setTickets] = useState<Ticket[]>([])

  const refreshTickets = useCallback(() => {
    if (user) {
      setTickets(getTicketsByUser(user.id))
    }
  }, [user])

  useEffect(() => {
    refreshTickets()
    const interval = setInterval(refreshTickets, 2000)
    return () => clearInterval(interval)
  }, [refreshTickets])

  const recentTickets = tickets.slice(0, MAX_RECENT)

  function handleNewTicket() {
    if (!user) return
    const ticket = createTicket(user.id)
    refreshTickets()
    router.push(`/chat/${ticket.id}`)
  }

  return (
    <Sidebar className="border-r-0">
      <SidebarHeader className="p-4">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
            <Monitor className="h-4 w-4" />
          </div>
          <div className="flex flex-col">
            <span className="text-sm font-semibold text-sidebar-foreground">IT HelpDesk</span>
            <span className="text-xs text-sidebar-foreground/60">Support Portal</span>
          </div>
        </div>
        <Button
          onClick={handleNewTicket}
          size="sm"
          className="mt-3 w-full gap-2"
        >
          <Plus className="h-4 w-4" />
          New Session
        </Button>
      </SidebarHeader>

      <SidebarContent>
        <ScrollArea className="h-[calc(100svh-280px)]">
          <SidebarGroup>
            <SidebarGroupLabel>
              <Clock className="mr-1 h-3 w-3" />
              Recent Sessions
            </SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {recentTickets.length === 0 ? (
                  <div className="px-3 py-6 text-center text-xs text-sidebar-foreground/50">
                    No sessions yet. Click above to create one.
                  </div>
                ) : (
                  recentTickets.map((ticket) => (
                    <SidebarMenuItem key={ticket.id}>
                      <SidebarMenuButton
                        isActive={activeId === ticket.id}
                        onClick={() => router.push(`/chat/${ticket.id}`)}
                        className="h-auto py-2.5 px-3"
                      >
                        <div className="flex w-full flex-col gap-1.5">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-mono text-sidebar-foreground/50">
                              {ticket.id}
                            </span>
                            <TicketStatusBadge status={ticket.status} />
                          </div>
                          <span className="truncate text-sm text-sidebar-foreground">
                            {ticket.title}
                          </span>
                        </div>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ))
                )}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </ScrollArea>
      </SidebarContent>

      <SidebarFooter className="p-3">
        <Button
          variant="ghost"
          size="sm"
          className="w-full justify-start gap-2 text-sidebar-foreground/70 hover:text-sidebar-foreground"
          onClick={() => router.push("/tickets")}
        >
          <History className="h-4 w-4" />
          View All Sessions
        </Button>
        <div className="flex items-center justify-between rounded-lg bg-sidebar-accent/50 px-3 py-2">
          <div className="flex flex-col">
            <span className="text-xs font-medium text-sidebar-foreground">
              {user?.name}
            </span>
            <span className="text-xs text-sidebar-foreground/50">
              {user?.email}
            </span>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-sidebar-foreground/50 hover:text-sidebar-foreground"
            onClick={logout}
          >
            <LogOut className="h-3.5 w-3.5" />
            <span className="sr-only">Sign out</span>
          </Button>
        </div>
      </SidebarFooter>
    </Sidebar>
  )
}
