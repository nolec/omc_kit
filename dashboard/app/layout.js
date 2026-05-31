import "./globals.css";

export const metadata = {
  title: "OMC Autopilot Dashboard",
  description: "Read-only dashboard for OMC autopilot runs",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
