import type { TicketStatus } from "./constants"

export interface TicketMessage {
  id: string
  role: "user" | "assistant"
  content: string
  createdAt: string
}

export interface Ticket {
  id: string
  title: string
  status: TicketStatus
  createdAt: string
  updatedAt: string
  userId: string
  messages: TicketMessage[]
}

const TICKETS_KEY = "helpdesk_tickets"
const COUNTER_KEY = "helpdesk_ticket_counter"

function getCounter(): number {
  if (typeof window === "undefined") return 0
  const val = localStorage.getItem(COUNTER_KEY)
  return val ? parseInt(val, 10) : 0
}

function setCounter(n: number): void {
  if (typeof window !== "undefined") {
    localStorage.setItem(COUNTER_KEY, String(n))
  }
}

export function getAllTickets(): Ticket[] {
  if (typeof window === "undefined") return []
  try {
    const stored = localStorage.getItem(TICKETS_KEY)
    if (!stored) return []
    return JSON.parse(stored) as Ticket[]
  } catch {
    return []
  }
}

function saveAllTickets(tickets: Ticket[]): void {
  if (typeof window !== "undefined") {
    localStorage.setItem(TICKETS_KEY, JSON.stringify(tickets))
  }
}

export function getTicketsByUser(userId: string): Ticket[] {
  return getAllTickets()
    .filter((t) => t.userId === userId)
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
}

export function getTicketById(ticketId: string): Ticket | null {
  return getAllTickets().find((t) => t.id === ticketId) || null
}

export function createTicket(userId: string): Ticket {
  const counter = getCounter() + 1
  setCounter(counter)
  const id = `TK-${String(counter).padStart(3, "0")}`
  const now = new Date().toISOString()
  const ticket: Ticket = {
    id,
    title: "New Session",
    status: "new",
    createdAt: now,
    updatedAt: now,
    userId,
    messages: [],
  }
  const tickets = getAllTickets()
  tickets.push(ticket)
  saveAllTickets(tickets)
  return ticket
}

export function updateTicketTitle(ticketId: string, title: string): void {
  const tickets = getAllTickets()
  const idx = tickets.findIndex((t) => t.id === ticketId)
  if (idx === -1) return
  tickets[idx].title = title
  tickets[idx].updatedAt = new Date().toISOString()
  saveAllTickets(tickets)
}

export function updateTicketStatus(ticketId: string, status: TicketStatus): void {
  const tickets = getAllTickets()
  const idx = tickets.findIndex((t) => t.id === ticketId)
  if (idx === -1) return
  tickets[idx].status = status
  tickets[idx].updatedAt = new Date().toISOString()
  saveAllTickets(tickets)
}

export function saveTicketMessages(ticketId: string, messages: TicketMessage[]): void {
  const tickets = getAllTickets()
  const idx = tickets.findIndex((t) => t.id === ticketId)
  if (idx === -1) return
  tickets[idx].messages = messages
  tickets[idx].updatedAt = new Date().toISOString()
  saveAllTickets(tickets)
}
