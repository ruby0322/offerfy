import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import DashboardTabs from "@/components/dashboard/DashboardTabs";

/**
 * The shell every dashboard view shares: the app nav, the six tabs, and the
 * footer. The views themselves render only their own content, so the tab bar
 * cannot disagree with itself between routes and switching views does not
 * remount the chrome.
 */
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="rr-shell">
      <Nav variant="app" />
      <DashboardTabs />
      {children}
      <Footer variant="app" />
    </div>
  );
}
