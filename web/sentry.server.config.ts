import * as Sentry from "@sentry/nextjs";

const dsn = process.env.SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    tracesSampleRate: 0,
    defaultIntegrations: false,
    integrations: [],
    beforeBreadcrumb: () => null,
    beforeSend: (event) => ({
      event_id: event.event_id,
      timestamp: event.timestamp,
      platform: "javascript",
      level: "error",
      message: "Call Intelligence web request error",
    } as typeof event),
  });
}
