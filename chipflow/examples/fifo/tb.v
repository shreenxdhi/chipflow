`timescale 1ns/1ps
module tb;
  reg clk=0, reset=1, push=0, pop=0;
  reg [7:0] data_in=0;
  wire [7:0] data_out;
  wire empty, full;
  integer i;
  fifo dut(clk,reset,push,pop,data_in,data_out,empty,full);
  always #5 clk=~clk;
  initial begin
    $dumpfile("waveform.vcd"); $dumpvars(0,tb);
    repeat(2) @(negedge clk);
    if(!empty || full) $fatal(1,"reset flags");
    reset=0;
    for(i=0;i<4;i=i+1) begin
      push=1; data_in=i+10; @(negedge clk);
    end
    push=0;
    if(!full || empty) $fatal(1,"full flags");
    push=1; data_in=99; @(negedge clk); push=0;
    for(i=0;i<4;i=i+1) begin
      if(data_out !== i+10) $fatal(1,"FIFO order");
      pop=1; @(negedge clk);
    end
    pop=0;
    if(!empty || full) $fatal(1,"empty flags");
    pop=1; @(negedge clk); pop=0;
    if(!empty) $fatal(1,"underflow changed count");
    push=1; data_in=77; @(negedge clk); push=0;
    if(data_out !== 77) $fatal(1,"pointer wrap");
    pop=1; @(negedge clk); pop=0;
    if(!empty) $fatal(1,"final empty");
    $display("PASS: FIFO order/full/empty/overflow/underflow/wrap"); $finish;
  end
  initial begin #2000; $fatal(1,"watchdog"); end
endmodule
