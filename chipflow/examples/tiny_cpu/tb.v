`timescale 1ns/1ps
module tb;
  reg clk=0, reset=1;
  wire [7:0] accumulator;
  wire done;
  wire [3:0] pc;
  tiny_cpu dut(clk,reset,accumulator,done,pc);
  always #5 clk=~clk;
  initial begin
    $dumpfile("waveform.vcd"); $dumpvars(0,tb);
    repeat(2) @(negedge clk);
    if(accumulator !== 0 || done !== 0 || pc !== 0) $fatal(1,"reset");
    reset=0; @(negedge clk);
    if(accumulator !== 5 || pc !== 1) $fatal(1,"LOAD");
    @(negedge clk);
    if(accumulator !== 8 || pc !== 2) $fatal(1,"ADD");
    @(negedge clk);
    if(accumulator !== 7 || pc !== 3) $fatal(1,"SUB");
    @(negedge clk);
    if(!done || accumulator !== 7 || pc !== 3) $fatal(1,"HALT");
    repeat(3) @(negedge clk);
    if(accumulator !== 7 || pc !== 3) $fatal(1,"halt stability");
    $display("PASS: LOAD/ADD/SUB/HALT architectural checks"); $finish;
  end
  initial begin #1000; $fatal(1,"watchdog"); end
endmodule
