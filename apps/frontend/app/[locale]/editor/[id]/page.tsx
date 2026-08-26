import EditorShell from "@/components/editor/EditorShell";
import { setRequestLocale } from "next-intl/server";
import { resolveLocale } from "@/lib/locale";

type Props = {
  params: Promise<{ locale: string; id: string }>;
};

export default async function EditorPage({ params }: Props) {
  const { locale: localeParam, id } = await params;
  const locale = resolveLocale(localeParam);
  setRequestLocale(locale);
  return <EditorShell resumeId={id} />;
}
