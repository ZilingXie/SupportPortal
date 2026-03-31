import { Badge } from "@/components/ui/badge"
import { TICKET_STATUS_CONFIG, type TicketStatus } from "@/lib/constants"

const statusColors: Record<TicketStatus, string> = {
  open: "bg-primary text-primary-foreground hover:bg-primary/90",
  communicating: "bg-warning text-warning-foreground hover:bg-warning/90",
  escalated: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
  investigating: "bg-warning text-warning-foreground hover:bg-warning/90",
  resolved: "bg-success text-success-foreground hover:bg-success/90",
}

export function TicketStatusBadge({ status }: { status: TicketStatus }) {
  const config = TICKET_STATUS_CONFIG[status]
  return (
    <Badge className={statusColors[status]}>
      {config.label}
    </Badge>
  )
}
