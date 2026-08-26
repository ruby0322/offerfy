"use client";

import { motion } from "framer-motion";

export default function LoadingMessage() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="flex justify-start py-1"
      aria-hidden="true"
    >
      <div className="flex space-x-1">
        <motion.div
          className="h-1.5 w-1.5 rounded-full bg-gray-400"
          animate={{ y: [0, -6, 0] }}
          transition={{ duration: 0.6, repeat: Infinity, delay: 0 }}
        />
        <motion.div
          className="h-1.5 w-1.5 rounded-full bg-gray-400"
          animate={{ y: [0, -6, 0] }}
          transition={{ duration: 0.6, repeat: Infinity, delay: 0.1 }}
        />
        <motion.div
          className="h-1.5 w-1.5 rounded-full bg-gray-400"
          animate={{ y: [0, -6, 0] }}
          transition={{ duration: 0.6, repeat: Infinity, delay: 0.2 }}
        />
      </div>
    </motion.div>
  );
}
