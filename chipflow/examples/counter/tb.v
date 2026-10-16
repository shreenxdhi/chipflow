`timescale 1ns/1ps
module tb;
  reg clk = 0;
  reg reset = 1;
  wire [7:0] count;
  counter dut(clk, reset, count);
  always #5 clk = ~clk;
  initial begin
    $dumpfile("waveform.vcd");
    $dumpvars(0, tb);
    repeat (2) @(negedge clk);
    if (count !== 0) $fatal(1, "reset failed");
    reset = 0;
    repeat (20) @(negedge clk);
    if (count !== 20) $fatal(1, "count failed: %d", count);
    $display("PASS: counter self-check");
    $finish;
  end
  initial begin #1000; $fatal(1, "watchdog"); end
endmodule
