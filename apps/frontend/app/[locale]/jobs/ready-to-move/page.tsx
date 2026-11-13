import { boardRoute } from "@/components/jobs/board-route";

// A static segment, so Next resolves this ahead of /jobs/[id].
const route = boardRoute("ready-to-move");

export const generateMetadata = route.generateMetadata;
export default route.Page;
