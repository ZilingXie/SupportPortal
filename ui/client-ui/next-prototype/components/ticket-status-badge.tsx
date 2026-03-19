import { Badge } from "@/components/ui/badge"
import { TICKET_STATUS_CONFIG, type TicketStatus } from "@/lib/constants"

const statusColors: Record<TicketStatus, string> = {
  new: "bg-primary text-primary-foreground hover:bg-primary/90",
  waiting_for_support: "bg-warning text-warning-foreground hover:bg-warning/90",
  waiting_for_agent: "bg-secondary text-secondary-foreground hover:bg-secondary/90",
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
