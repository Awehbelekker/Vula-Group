/**
 * VulaEmailCampaigns.jsx — bulk/campaign email, distinct from the 1:1 business
 * correspondence in the Assistant's email skill. Reuses the exact same audience
 * targeting (built-in filters + saved segments) as WhatsApp Broadcast — a customer's
 * email is already merged into the same aggregated customer list.
 */
import EmailCampaignsV2 from './broadcast/EmailCampaignsV2'

export default function VulaEmailCampaigns({ ...props }) {
  return <EmailCampaignsV2 {...props} />
}

