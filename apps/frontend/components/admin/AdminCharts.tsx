"use client";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import type { AdminCounts, AdminDayPoint } from "@/lib/api";
import { useTranslations } from "next-intl";
import { Bar, BarChart, CartesianGrid, Line, LineChart, Pie, PieChart, XAxis, YAxis } from "recharts";

type Props = {
  series: AdminDayPoint[];
  counts: AdminCounts;
};

function shortDay(iso: string) {
  const parts = iso.split("-");
  return parts.length === 3 ? `${parts[1]}-${parts[2]}` : iso;
}

function axisProps() {
  return {
    tickLine: false,
    axisLine: false,
    tickMargin: 8,
    minTickGap: 16,
  } as const;
}

export default function AdminCharts({ series, counts }: Props) {
  const t = useTranslations("admin");

  const activityConfig = {
    users: { label: t("chartUsers"), color: "var(--chart-1)" },
    resumes: { label: t("chartResumes"), color: "var(--chart-2)" },
    chats: { label: t("chartChats"), color: "var(--chart-3)" },
  } satisfies ChartConfig;

  const sourceConfig = {
    resumes_create: { label: t("sourceCreate"), color: "var(--chart-1)" },
    resumes_upload: { label: t("sourceUpload"), color: "var(--chart-4)" },
  } satisfies ChartConfig;

  const ownerConfig = {
    user: { label: t("ownerUser"), color: "var(--chart-2)" },
    guest: { label: t("ownerGuest"), color: "var(--chart-1)" },
  } satisfies ChartConfig;

  const rateConfig = {
    guest_rate_chat: { label: t("chartRateChat"), color: "var(--chart-3)" },
    guest_rate_export: { label: t("chartRateExport"), color: "var(--chart-5)" },
  } satisfies ChartConfig;

  const activity = series.map((row) => ({
    date: shortDay(row.date),
    users: row.users,
    resumes: row.resumes_create + row.resumes_upload,
    chats: row.chats,
  }));

  const sources = series.map((row) => ({
    date: shortDay(row.date),
    resumes_create: row.resumes_create,
    resumes_upload: row.resumes_upload,
  }));

  const rates = series.map((row) => ({
    date: shortDay(row.date),
    guest_rate_chat: row.guest_rate_chat,
    guest_rate_export: row.guest_rate_export,
  }));

  const ownerMix = [
    { key: "user", value: counts.resumes_user, fill: "var(--color-user)" },
    { key: "guest", value: counts.resumes_guest, fill: "var(--color-guest)" },
  ];
  const sourceMix = [
    { key: "resumes_create", value: counts.resumes_create, fill: "var(--color-resumes_create)" },
    { key: "resumes_upload", value: counts.resumes_upload, fill: "var(--color-resumes_upload)" },
  ];

  const activityTotal = activity.reduce((sum, row) => sum + row.users + row.resumes + row.chats, 0);
  const sourceTotal = sources.reduce((sum, row) => sum + row.resumes_create + row.resumes_upload, 0);
  const rateTotal = rates.reduce((sum, row) => sum + row.guest_rate_chat + row.guest_rate_export, 0);
  const ownerTotal = ownerMix.reduce((sum, row) => sum + row.value, 0);
  const sourceMixTotal = sourceMix.reduce((sum, row) => sum + row.value, 0);

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>{t("chartActivity")}</CardTitle>
          <CardDescription>{t("chartActivityHint")}</CardDescription>
        </CardHeader>
        <CardContent>
          {activityTotal === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">{t("chartNoData")}</p>
          ) : (
            <ChartContainer config={activityConfig} className="aspect-[2.2/1] w-full">
              <LineChart data={activity} margin={{ left: 4, right: 8, top: 8 }}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="date" {...axisProps()} />
                <YAxis width={28} allowDecimals={false} tickLine={false} axisLine={false} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <ChartLegend content={<ChartLegendContent />} />
                <Line dataKey="users" type="monotone" stroke="var(--color-users)" strokeWidth={2} dot={false} />
                <Line dataKey="resumes" type="monotone" stroke="var(--color-resumes)" strokeWidth={2} dot={false} />
                <Line dataKey="chats" type="monotone" stroke="var(--color-chats)" strokeWidth={2} dot={false} />
              </LineChart>
            </ChartContainer>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("chartSource")}</CardTitle>
          <CardDescription>{t("chartSourceHint")}</CardDescription>
        </CardHeader>
        <CardContent>
          {sourceTotal === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">{t("chartNoData")}</p>
          ) : (
            <ChartContainer config={sourceConfig} className="aspect-[1.6/1] w-full">
              <BarChart data={sources} margin={{ left: 4, right: 8, top: 8 }}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="date" {...axisProps()} />
                <YAxis width={28} allowDecimals={false} tickLine={false} axisLine={false} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <ChartLegend content={<ChartLegendContent />} />
                <Bar dataKey="resumes_create" stackId="s" fill="var(--color-resumes_create)" radius={[0, 0, 0, 0]} />
                <Bar dataKey="resumes_upload" stackId="s" fill="var(--color-resumes_upload)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ChartContainer>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("chartRates")}</CardTitle>
          <CardDescription>{t("chartRatesHint")}</CardDescription>
        </CardHeader>
        <CardContent>
          {rateTotal === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">{t("chartNoData")}</p>
          ) : (
            <ChartContainer config={rateConfig} className="aspect-[1.6/1] w-full">
              <LineChart data={rates} margin={{ left: 4, right: 8, top: 8 }}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="date" {...axisProps()} />
                <YAxis width={28} allowDecimals={false} tickLine={false} axisLine={false} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <ChartLegend content={<ChartLegendContent />} />
                <Line
                  dataKey="guest_rate_chat"
                  type="monotone"
                  stroke="var(--color-guest_rate_chat)"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  dataKey="guest_rate_export"
                  type="monotone"
                  stroke="var(--color-guest_rate_export)"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ChartContainer>
          )}
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>{t("chartMix")}</CardTitle>
          <CardDescription>{t("chartMixHint")}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div>
            <p className="mb-2 text-xs text-muted-foreground">{t("chartOwner")}</p>
            {ownerTotal === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">{t("chartNoData")}</p>
            ) : (
              <ChartContainer config={ownerConfig} className="mx-auto aspect-square max-h-52">
                <PieChart>
                  <ChartTooltip content={<ChartTooltipContent hideLabel nameKey="key" />} />
                  <Pie data={ownerMix} dataKey="value" nameKey="key" innerRadius={48} strokeWidth={2} />
                  <ChartLegend content={<ChartLegendContent nameKey="key" />} />
                </PieChart>
              </ChartContainer>
            )}
          </div>
          <div>
            <p className="mb-2 text-xs text-muted-foreground">{t("source")}</p>
            {sourceMixTotal === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">{t("chartNoData")}</p>
            ) : (
              <ChartContainer config={sourceConfig} className="mx-auto aspect-square max-h-52">
                <PieChart>
                  <ChartTooltip content={<ChartTooltipContent hideLabel nameKey="key" />} />
                  <Pie data={sourceMix} dataKey="value" nameKey="key" innerRadius={48} strokeWidth={2} />
                  <ChartLegend content={<ChartLegendContent nameKey="key" />} />
                </PieChart>
              </ChartContainer>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
