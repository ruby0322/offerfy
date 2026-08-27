import { BlogValidationError } from "../lib/blog/types";
import { validateBlogTree } from "../lib/blog/validate";

try {
  const posts = validateBlogTree();
  console.log(`ok: ${posts.length} post(s)`);
} catch (error) {
  if (error instanceof BlogValidationError) {
    console.error(error.message);
    process.exit(1);
  }
  throw error;
}
