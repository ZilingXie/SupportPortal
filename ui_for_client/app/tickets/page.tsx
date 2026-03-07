"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "@/components/auth-provider"
import { TicketStatusBadge } from "@/components/ticket-status-badge"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { getTicketsByUser, updateTicketStatus, type Ticket } from "@/lib/tickets"
import { TICKET_STATUS_CONFIG, type TicketStatus } from "@/lib/constants"
import {
  ArrowLeft,
  CheckCircle,
  ExternalLink,
  Monitor,
  RotateCcw,
} from "lucide-react"
import { toast } from "sonner"

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export default function TicketsPage() {
  const { user, isLoading } = useAuth()
  const router = useRouter()
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [statusFilter, setStatusFilter] = useState<string>("all")

  useEffect(() => {
    if (!isLoading && !user) {
      router.push("/login")
    }
  }, [user, isLoading, router])

  useEffect(() => {
    if (user) {
      setTickets(getTicketsByUser(user.id))
    }
  }, [user])

  function refreshTickets() {
    if (user) {
      setTickets(getTicketsByUser(user.id))
    }
  }

  function handleResolve(ticketId: string) {
    updateTicketStatus(ticketId, "resolved")
    refreshTickets()
    toast.success("Session marked as resolved")
  }

  function handleReopen(ticketId: string) {
    updateTicketStatus(ticketId, "waiting_for_support")
    refreshTickets()
    toast.success("Session reopened")
  }

  const filtered =
    statusFilter === "all"
      ? tickets
      : tickets.filter((t) => t.status === statusFilter)

  if (isLoading) {
    return (
      <div className="flex h-svh items-center justify-center bg-background">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    )
  }

  if (!user) return null

  return (
    <div className="flex min-h-svh flex-col bg-background">
      {/* Top Bar */}
      <header className="flex items-center justify-between border-b px-6 py-4">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => router.push("/chat")}
            className="h-8 w-8"
          >
            <ArrowLeft className="h-4 w-4" />
            <span className="sr-only">Back to chat</span>
          </Button>
          <div className="flex items-center gap-2">
            <Monitor className="h-5 w-5 text-primary" />
            <h1 className="text-lg font-semibold text-foreground">Session History</h1>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-[180px]">
              <SelectValue placeholder="Filter by status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Statuses</SelectItem>
              {(Object.keys(TICKET_STATUS_CONFIG) as TicketStatus[]).map((s) => (
                <SelectItem key={s} value={s}>
                  {TICKET_STATUS_CONFIG[s].label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </header>

      {/* Table */}
      <div className="flex-1 p-6">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <p className="text-muted-foreground">No sessions found</p>
          </div>
        ) : (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[100px]">Session ID</TableHead>
                  <TableHead>Title</TableHead>
                  <TableHead className="w-[160px]">Status</TableHead>
                  <TableHead className="w-[150px]">Created</TableHead>
                  <TableHead className="w-[150px]">Updated</TableHead>
                  <TableHead className="w-[180px] text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((ticket) => (
                  <TableRow key={ticket.id}>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {ticket.id}
                    </TableCell>
                    <TableCell className="font-medium">{ticket.title}</TableCell>
                    <TableCell>
                      <TicketStatusBadge status={ticket.status} />
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDate(ticket.createdAt)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDate(ticket.updatedAt)}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="gap-1.5"
                          onClick={() => router.push(`/chat/${ticket.id}`)}
                        >
                          <ExternalLink className="h-3.5 w-3.5" />
                          View
                        </Button>
                        {ticket.status === "resolved" ? (
                          <Button
                            variant="outline"
                            size="sm"
                            className="gap-1.5"
                            onClick={() => handleReopen(ticket.id)}
                          >
                            <RotateCcw className="h-3.5 w-3.5" />
                            Reopen
                          </Button>
                        ) : (
                          <Button
                            variant="outline"
                            size="sm"
                            className="gap-1.5"
                            onClick={() => handleResolve(ticket.id)}
                          >
                            <CheckCircle className="h-3.5 w-3.5" />
                            Resolve
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </div>
  )
}
