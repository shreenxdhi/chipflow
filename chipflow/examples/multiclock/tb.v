`timescale 1ns/1ps
module tb;
  reg clk_a=0, clk_b=0, reset=1;
  wire [7:0] count_a, count_b;
  multiclock dut(clk_a,clk_b,reset,count_a,count_b);
  always #5 clk_a=~clk_a;
  always #7 clk_b=~clk_b;
  initial begin
    $dumpfile("waveform.vcd"); $dumpvars(0,tb);
    #30;
    if(count_a !== 0 || count_b !== 0) $fatal(1,"reset");
    reset=0;
    #110;
    // Rising edges in (30,140): A=35..135 (11), B=35..133 (8).
    if(count_a !== 11 || count_b !== 8) $fatal(1,"independent domain counts");
    $display("PASS: independent clocks; no CDC/RDC proof claimed"); $finish;
  end
  initial begin #1000; $fatal(1,"watchdog"); end
endmodule
