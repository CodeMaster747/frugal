import { z } from "zod";

/**
 * Client-side validation mirrors the backend's rules (FR-1.1) so the user gets
 * immediate feedback. The server revalidates regardless -- this is a courtesy,
 * never a control.
 */
export const passwordSchema = z
  .string()
  .min(12, "At least 12 characters")
  .max(128, "At most 128 characters")
  .regex(/[a-z]/, "Needs a lowercase letter")
  .regex(/[A-Z]/, "Needs an uppercase letter")
  .regex(/\d/, "Needs a digit");

export const loginSchema = z.object({
  email: z.string().min(1, "Email is required").email("Enter a valid email"),
  password: z.string().min(1, "Password is required"),
});

export const registerSchema = z.object({
  display_name: z.string().min(1, "Name is required").max(120),
  email: z.string().min(1, "Email is required").email("Enter a valid email"),
  password: passwordSchema,
});

export type LoginValues = z.infer<typeof loginSchema>;
export type RegisterValues = z.infer<typeof registerSchema>;
